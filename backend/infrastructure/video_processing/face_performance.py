"""从实拍走位提取角色局部，按同一时间轴回贴连续生成的面部。"""

import math
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Timer

import cv2
import numpy as np
from PIL import Image, ImageOps

from .desktop import scene_change_times
from .face_detail import _MODEL, _face_box, _native_face_crop, _session, review_model_path
from .ffmpeg import VideoProcessError, VideoToolUnavailableError, _binary, probe_video, run_ffmpeg, video_frame_times

_LOCAL_PROCESS_TIMEOUT = 600.0


@dataclass(frozen=True)
class FaceTrack:
    size: tuple[int, int]
    # 每帧在全景中的中心及去除定位外缘的宽高，均使用交付像素。
    geometry: np.ndarray
    frames: tuple[int, ...]


@contextmanager
def _frames(
    path: Path, size: tuple[int, int], *, origin: tuple[int, int] | None = None
) -> Iterator[Iterator[Image.Image]]:
    w, h = size
    crop = f"crop={w}:{h}:{origin[0]}:{origin[1]}:exact=1," if origin else ""
    with subprocess.Popen(
        [
            _binary("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"{crop}scale={w}:{h},setsar=1",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        expired = Event()

        def stop_decoder() -> None:
            expired.set()
            proc.kill()

        def images() -> Iterator[Image.Image]:
            while data := proc.stdout.read(w * h * 3):
                if len(data) != w * h * 3:
                    if expired.is_set():
                        raise VideoToolUnavailableError("局部视频解码超时")
                    raise VideoProcessError("面部运动帧不完整")
                yield Image.frombytes("RGB", size, data)
            if proc.wait():
                if expired.is_set():
                    raise VideoToolUnavailableError("局部视频解码超时")
                raise VideoProcessError("面部运动帧无法解码")

        deadline = Timer(_LOCAL_PROCESS_TIMEOUT, stop_decoder)
        deadline.start()
        try:
            yield images()
        finally:
            deadline.cancel()
            deadline.join()
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


def _geometry(path: Path, size: tuple[int, int], count: int, *, allow_gaps: bool = False) -> np.ndarray | None:
    model = review_model_path(_MODEL)
    if not model.is_file():
        return None
    session = _session(model, model.stat().st_mtime_ns)
    scale = min(1.0, 768 / max(size))
    sw, sh = (max(2, round(v * scale)) for v in size)
    sx, sy = size[0] / sw, size[1] / sh
    boxes = []
    with _frames(path, (sw, sh)) as stream:
        for image in stream:
            with image:
                box = _face_box(image, session, strict_single=True)
            # 遮挡、多人和出画保留原摄制，不在无依据时补脸。
            if not box:
                if not allow_gaps:
                    return None
                boxes.append((np.nan,) * 4)
                continue
            left, top, right, bottom = box
            boxes.append(
                ((left + right) * sx / 2, (top + bottom) * sy / 2, (right - left) * sx / 1.9, (bottom - top) * sy / 1.7)
            )
    if len(boxes) != count:
        return None
    raw = np.asarray(boxes)
    found = np.isfinite(raw[:, 0])
    if not found.all():
        if np.count_nonzero(found) < 2:
            return None
        # 仅供相邻可见画格平滑定位。缺失的画格随后标回，合成时完全使用底片。
        rows = np.arange(count)
        raw[~found] = np.column_stack([np.interp(rows[~found], rows[found], series[found]) for series in raw.T])
    if np.any(np.linalg.norm(np.diff(raw[:, :2], axis=0), axis=1) > raw[:-1, 3] * 0.5):
        return None
    # 只消除定位框的逐帧抖动，身体、头和眼睛的实际运动都来自画面。
    pad = np.pad(raw, ((2, 2), (0, 0)), mode="edge")
    median = np.median(np.lib.stride_tricks.sliding_window_view(pad, 5, axis=0), axis=-1)
    kernel = np.exp(-0.5 * (np.arange(-5, 6) / 1.7) ** 2)
    padded = np.pad(median, ((5, 5), (0, 0)), mode="edge")
    result = np.average(np.lib.stride_tricks.sliding_window_view(padded, 11, axis=0), weights=kernel, axis=-1)
    result[~found] = np.nan
    return result


def track_performance(path: Path, portrait: bytes, *, frame_count: int) -> FaceTrack | None:
    model = review_model_path(_MODEL)
    if not model.is_file():
        return None
    with Image.open(BytesIO(portrait)) as source:
        ImageOps.exif_transpose(source, in_place=True)
        if _face_box(source, _session(model, model.stat().st_mtime_ns), strict_single=True) is None:
            return None
    probe = probe_video(path)
    times = video_frame_times(path)
    if probe.has_alpha or len(times) != frame_count or not np.allclose(np.diff(times), 1 / 24, atol=0.001):
        return None
    if scene_change_times(path):
        return None
    size = probe.width, probe.height
    geometry = _geometry(path, size, frame_count)
    if geometry is None:
        return None
    # 原画面中面部本已占满近景时，沿用其原生表演和光学细节。
    if np.median(geometry[:, 3]) > min(size) * 0.46:
        return None
    intervals = min(7, max(2, math.ceil((frame_count - 1) / 42)))
    indices = (0, *(17 * round((frame_count - 1) * i / (17 * intervals)) for i in range(1, intervals)), frame_count - 1)
    return FaceTrack(size, geometry, indices)


def performance_anchors(path: Path, track: FaceTrack) -> tuple[bytes, ...]:
    width, height = track.size
    half = min(min(track.size) // 2, max(16, round(np.quantile(track.geometry[:, 3], 0.9) * 1.65)))
    anchors = []
    for index in track.frames:
        cx, cy = track.geometry[index, :2]
        x = min(width - 2 * half, max(0, round(cx) - half))
        y = min(height - 2 * half, max(0, round(cy) - half))
        with (
            _native_face_crop(
                path, index, track.size, (x, y, x + 2 * half, y + 2 * half), None, (), with_alpha=False
            ) as frame,
            frame.resize((768, 768), Image.Resampling.LANCZOS) as canvas,
            BytesIO() as out,
        ):
            canvas.save(out, format="PNG")
            anchors.append(out.getvalue())
    return tuple(anchors)


def _smoothstep(value: np.ndarray) -> np.ndarray:
    x = np.clip(value, 0, 1)
    return x * x * (3 - 2 * x)


def _restore_on_performance(
    original: Image.Image, sharpened: Image.Image, width: float, matcher: cv2.DISOpticalFlow, contrast: cv2.CLAHE
) -> tuple[Image.Image, np.ndarray]:
    """将细节配准到原表演；不一致的眼睑、口形和遮挡保留原片像素。"""
    base = np.asarray(original)
    refined = np.asarray(sharpened)
    base_luma, refined_luma = (cv2.cvtColor(v, cv2.COLOR_RGB2GRAY) for v in (base, refined))
    base_edges, refined_edges = (cv2.GaussianBlur(contrast.apply(v), (0, 0), 0.85) for v in (base_luma, refined_luma))
    forward = matcher.calc(base_edges, refined_edges, None)
    backward = matcher.calc(refined_edges, base_edges, None)
    yy, xx = np.indices(base_luma.shape, dtype=np.float32)
    sampling = np.dstack((xx, yy)) + forward

    def warp(values: np.ndarray) -> np.ndarray:
        return cv2.remap(values, sampling, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # 正反向核对能排除消失的表面，同一软边缘两侧的局部相关性用于排除重影。
    round_trip = np.linalg.norm(forward + warp(backward), axis=2)
    warped = warp(refined)
    a = base_luma.astype(np.float32) / 255
    b = warp(refined_luma).astype(np.float32) / 255
    radius = max(2.0, width * 0.065)

    def mean(values: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(values, (0, 0), radius)

    avg_a, avg_b = mean(a), mean(b)
    var_a = np.maximum(0, mean(a * a) - avg_a**2)
    var_b = np.maximum(0, mean(b * b) - avg_b**2)
    correlation = (mean(a * b) - avg_a * avg_b) / np.sqrt(var_a * var_b + 1e-8)
    agrees = (
        (correlation > 0.64) & (round_trip < max(1.6, 0.021 * width)) & (np.linalg.norm(forward, axis=2) < 0.15 * width)
    )
    margin = max(2, round(width * 0.03))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
    interior = cv2.erode(agrees.astype(np.uint8), kernel)
    weight = cv2.GaussianBlur(interior.astype(np.float32), (0, 0), max(1.0, width * 0.02))
    # 大尺度色调跟随当时现场的受光，只从配准后的同一表面取回更细的结构。
    colors = warped.astype(np.float32)
    colors += cv2.GaussianBlur(base.astype(np.float32) - colors, (0, 0), max(3.0, width * 0.15))
    return Image.fromarray(np.clip(colors, 0, 255).astype(np.uint8)), weight


def compose_face_performance(
    source: Path, detail: Path, track: FaceTrack, *, keep_first: bool, keep_last: bool
) -> bytes:
    count = len(track.geometry)
    clip = probe_video(detail)
    times = video_frame_times(detail)
    if len(times) != count or not np.allclose(np.diff(times), 1 / 24, atol=0.001):
        raise VideoProcessError("局部与原镜头时间轴不一致")
    patch_size = clip.width, clip.height
    localized = _geometry(detail, patch_size, count, allow_gaps=True)
    if localized is None:
        raise VideoProcessError("局部运动无法逐帧对齐到同一张脸")
    gaps = np.flatnonzero(~np.isfinite(localized[:, 0]))
    visibility = (
        np.minimum(1.0, np.abs(np.arange(count)[:, None] - gaps).min(axis=1) / 5) if gaps.size else np.ones(count)
    )
    cx, cy, w, h = track.geometry.T
    left = int(max(0, np.floor(np.min(cx - 1.06 * w)))) // 2 * 2
    top = int(max(0, np.floor(np.min(cy - 1.04 * h)))) // 2 * 2
    right = min(track.size[0], 2 * math.ceil(np.max(cx + 1.06 * w) / 2))
    bottom = min(track.size[1], 2 * math.ceil(np.max(cy + 0.80 * h) / 2))
    region = right - left, bottom - top
    yy, xx = np.ogrid[top:bottom, left:right]
    matcher = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    matcher.setFinestScale(0)
    contrast = cv2.createCLAHE(clipLimit=2, tileGridSize=(6, 6))
    with TemporaryDirectory(prefix="spirit-face-") as scratch:
        root = Path(scratch)
        with _frames(detail, patch_size) as frames, _frames(source, region, origin=(left, top)) as originals:
            for index, (frame, base) in enumerate(zip(frames, originals, strict=True)):
                if not visibility[index]:
                    with frame, base, Image.new("RGBA", region) as transparent:
                        transparent.save(root / f"{index:04d}.png")
                    continue
                x, y, fw, fh = track.geometry[index]
                px, py, _, ph = localized[index]
                scale = fh / ph
                # 羽化限于脸及上颈，衣领、发饰和原身形继续使用原全景。
                radius = np.hypot((xx - x) / (1.04 * fw), (yy - y + 0.10 * fh) / (0.92 * fh))
                mask = _smoothstep((1 - radius) / 0.23) * _smoothstep((y + 0.70 * fh - yy) / (0.18 * fh))
                fade = min(
                    visibility[index], index / 5 if keep_first else 1.0, (count - 1 - index) / 5 if keep_last else 1.0
                )
                with (
                    frame,
                    base,
                    frame.transform(
                        region,
                        Image.Transform.AFFINE,
                        (1 / scale, 0, px + (left - x) / scale, 0, 1 / scale, py + (top - y) / scale),
                        resample=Image.Resampling.BICUBIC,
                    ) as tile,
                ):
                    restored, agreement = _restore_on_performance(base, tile, fw, matcher, contrast)
                    with restored:
                        restored.putalpha(Image.fromarray(np.uint8(mask * agreement * fade * 255)))
                        restored.save(root / f"{index:04d}.png", compress_level=1)
        out = root / "result.mp4"
        run_ffmpeg(
            [
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-framerate",
                "24",
                "-i",
                str(root / "%04d.png"),
                "-filter_complex",
                f"[0:v][1:v]overlay={left}:{top}:shortest=1[v]",
                "-map",
                "[v]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-crf",
                "14",
                "-preset",
                "fast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ],
            label="角色视频局部合成",
            timeout=_LOCAL_PROCESS_TIMEOUT,
        )
        if len(video_frame_times(out)) != count:
            raise VideoProcessError("局部合成未保留原帧数")
        return out.read_bytes()
