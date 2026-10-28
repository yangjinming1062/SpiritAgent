"""逐帧透明化主体视频，并交付可独立播放的 WebM 与同步遮罩。"""

import base64
import json
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

from infrastructure.video_processing.desktop import extract_desktop_first_frame
from infrastructure.video_processing.ffmpeg import (
    VideoProcessError,
    alpha_input_args,
    ffmpeg_stdout,
    probe_video,
    run_ffmpeg,
)
from infrastructure.video_processing.matting import ForegroundMatte, require_matting_model

_FRAME_RATE = 24
_MAX_EDGE = 1024


@dataclass(frozen=True)
class ActorClipMedia:
    video_path: Path
    poster_path: Path
    width: int
    height: int
    duration_ms: int
    first_frame_path: Path
    last_frame_path: Path
    mask_path: Path
    foot_anchor: tuple[int, int]
    body_bounds: tuple[int, int, int, int]
    hit_bounds: tuple[int, int, int, int]


def _bounds(alpha: np.ndarray) -> tuple[int, int, int, int]:
    rows, cols = np.nonzero(alpha > 8)
    if not rows.size:
        raise VideoProcessError("透明主体视频缺少可见主体")
    return int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1


def _validate_alpha(alpha: np.ndarray) -> None:
    coverage = float((alpha > 128).mean())
    if not 0.02 <= coverage <= 0.9 or float((alpha <= 8).mean()) < 0.10:
        raise VideoProcessError("透明主体视频的主体或透明背景不完整，请重新生成")


def _verify_alpha(video: Path, width: int, height: int) -> None:
    probe = probe_video(video)
    if not probe.has_alpha:
        raise VideoProcessError("透明主体视频缺少透明通道")
    # 透明辅助层可能只在部分帧损坏；只解码首帧无法证明整段主体都能合成。
    # 以小画幅解码完整时间轴，避免把整段 RGBA 原始帧一次性读入内存。
    verify_width = min(width, 64)
    verify_height = min(height, 64)
    raw = ffmpeg_stdout(
        [
            *alpha_input_args(probe),
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"scale={verify_width}:{verify_height},format=rgba",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-",
        ],
        failure_message="透明主体视频解码失败",
    )
    frame_size = verify_width * verify_height * 4
    if len(raw) == 0 or len(raw) % frame_size:
        raise VideoProcessError("透明主体视频时间轴无法完整解码")
    if len(raw) < frame_size * 2:
        raise VideoProcessError("透明主体视频帧数不足")
    for index in range(len(raw) // frame_size):
        alpha = np.frombuffer(raw, dtype=np.uint8, count=frame_size, offset=index * frame_size)
        try:
            _validate_alpha(alpha.reshape(verify_height, verify_width, 4)[..., 3])
        except VideoProcessError:
            raise VideoProcessError(f"透明主体视频第 {index + 1} 帧主体或透明背景不完整，请重新生成") from None


def prepare_actor_clip(
    src: Path,
    *,
    dst: Path,
    poster_dst: Path,
    mask_dst: Path,
    first_frame_dst: Path,
    last_frame_dst: Path,
) -> ActorClipMedia:
    """固定到 24 fps 后逐帧处理；帧落临时目录，避免整片 RGB/alpha 常驻内存。"""

    model = require_matting_model()
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120 or probe.duration_seconds > 60:
        raise VideoProcessError("透明主体视频尺寸、帧率或时长超出处理上限")
    with Image.open(BytesIO(extract_desktop_first_frame(src))) as first:
        scale = min(1.0, _MAX_EDGE / max(first.size))
        width, height = (max(2, round(value * scale / 2) * 2) for value in first.size)
    for path in (dst, poster_dst, mask_dst, first_frame_dst, last_frame_dst):
        path.parent.mkdir(parents=True, exist_ok=True)
    mask_frames: list[dict[str, float | str]] = []
    union: tuple[int, int, int, int] | None = None
    first_bounds: tuple[int, int, int, int] | None = None
    foot = (0, 0)
    with TemporaryDirectory(prefix="actor-", dir=dst.parent) as temporary:
        pattern = str(Path(temporary) / "%06d.png")
        run_ffmpeg(
            [
                "-i",
                str(src),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                f"scale={width}:{height},setsar=1,fps={_FRAME_RATE}",
                pattern,
            ],
            label="主体视频逐帧解码",
        )
        frames = sorted(Path(temporary).glob("*.png"))
        if len(frames) < 2 or len(frames) > 60 * _FRAME_RATE:
            raise VideoProcessError("透明主体视频帧数超出处理范围")
        matte = ForegroundMatte(model)
        for index, path in enumerate(frames):
            with Image.open(path) as frame:
                rgba = matte.apply(frame.convert("RGB"))
            alpha = np.asarray(rgba.getchannel("A"))
            _validate_alpha(alpha)
            bounds = _bounds(alpha)
            union = (
                bounds
                if union is None
                else (
                    min(union[0], bounds[0]),
                    min(union[1], bounds[1]),
                    max(union[2], bounds[2]),
                    max(union[3], bounds[3]),
                )
            )
            rgba.save(path, format="PNG")
            if index == 0:
                first_bounds = bounds
                bottom = bounds[3] - 1
                columns = np.nonzero(alpha[bottom] > 8)[0]
                foot = (int((columns[0] + columns[-1]) // 2), bottom)
                rgba.save(first_frame_dst, format="PNG")
                rgba.save(poster_dst, format="WEBP", lossless=True)
            if index == len(frames) - 1:
                rgba.save(last_frame_dst, format="PNG")
            with BytesIO() as output:
                rgba.getchannel("A").resize((max(2, width // 4), max(2, height // 4)), Image.Resampling.BILINEAR).save(
                    output,
                    format="PNG",
                    optimize=True,
                )
                mask_frames.append(
                    {
                        "t": index / _FRAME_RATE,
                        "png": base64.b64encode(zlib.compress(output.getvalue())).decode("ascii"),
                    }
                )
        # 编码输入必须是已经透明化的帧，不能重新编码供应商的不透明原片。
        run_ffmpeg(
            [
                "-framerate",
                str(_FRAME_RATE),
                "-i",
                pattern,
                "-an",
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                "-lag-in-frames",
                "0",
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                "-b:v",
                "0",
                "-crf",
                "32",
                str(dst),
            ],
            label="透明主体视频编码",
        )
    _verify_alpha(dst, width, height)
    if first_bounds is None or union is None:
        raise VideoProcessError("透明主体视频缺少主体边界")
    mask_dst.write_text(
        json.dumps({"kind": "frames", "width": width, "height": height, "fps": _FRAME_RATE, "frames": mask_frames}),
        encoding="utf-8",
    )
    return ActorClipMedia(
        video_path=dst,
        poster_path=poster_dst,
        width=width,
        height=height,
        duration_ms=round(len(mask_frames) / _FRAME_RATE * 1000),
        first_frame_path=first_frame_dst,
        last_frame_path=last_frame_dst,
        mask_path=mask_dst,
        foot_anchor=foot,
        body_bounds=first_bounds,
        hit_bounds=union,
    )
