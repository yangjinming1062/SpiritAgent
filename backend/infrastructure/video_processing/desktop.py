"""桌面影像视频校验与适配；原始上传保留，派生视频按目标画幅裁切。"""

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from infrastructure.media_geometry import centered_crop_box
from infrastructure.video_processing.face_comparison import face_structure_loss_times
from infrastructure.video_processing.face_detail import compare_face_sequences, review_face_details
from infrastructure.video_processing.ffmpeg import (
    VideoProcessError,
    alpha_input_args,
    ffmpeg_stdout,
    probe_video,
    run_ffmpeg,
    video_frame_times,
)
from infrastructure.video_processing.scene_timeline import OPENING_SCENE_SAMPLE_OFFSETS, opening_scene_evidence


@dataclass(frozen=True)
class DesktopVideoResult:
    width: int
    height: int
    duration_ms: int
    cover: bytes
    first_frame: bytes
    fitted: bool = False


@dataclass(frozen=True)
class DesktopVideoFrames:
    frames: tuple[bytes, ...]
    times_seconds: tuple[float, ...]
    face_details: tuple[tuple[float, bytes], ...] = ()
    face_transition: bytes | None = None
    scene_transition: bytes | None = None
    face_transient_times: tuple[float, ...] = ()


def scene_change_times(src: Path) -> tuple[float, ...]:
    metadata = ffmpeg_stdout(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "setpts=PTS-STARTPTS,scale=96:64,select='gt(scene,0.25)',metadata=mode=print:key=lavfi.scene_score:file=-",
            "-f",
            "null",
            "-",
        ],
        failure_message="视频转场采样失败",
    )
    changes = re.findall(rb"pts_time:([\d.eE+-]+)\s+lavfi\.scene_score=([\d.eE+-]+)", metadata)
    ranked = sorted(((float(score), float(time)) for time, score in changes), key=lambda item: -item[0])
    return tuple(time for _score, time in ranked[:2])


def sample_desktop_video_frames(
    src: Path,
    *,
    sample_count: int = 5,
    max_edge: int = 1024,
    preserve_alpha: bool = False,
    background: tuple[int, int, int] | None = None,
    include_opening_transition: bool = False,
    include_scene_changes: bool = False,
    include_face_details: bool = False,
    at_times: tuple[float, ...] = (),
) -> DesktopVideoFrames:
    """按实际帧时间抽取首尾和中间帧，支持原始帧率、VFR、旋转和非方形像素。"""
    if sample_count < 2:
        raise ValueError("视频采样至少需要首尾两帧")
    if max_edge < 2:
        raise ValueError("视频采样尺寸无效")
    times = video_frame_times(src)
    first, last = times[0], times[-1]
    if at_times:
        targets = [first + offset for offset in at_times]
    else:
        targets = [first + (last - first) * fraction / (sample_count - 1) for fraction in range(sample_count)]
        if include_opening_transition:
            targets.extend(first + offset for offset in (0.25, 0.5) if first + offset < last)
        if include_scene_changes:
            targets.extend(first + offset for offset in scene_change_times(src))

    def closest_frame(target: float) -> int:
        return min(range(len(times)), key=lambda index: abs(times[index] - target))

    indices = tuple(sorted({closest_frame(target) for target in targets}))
    check_opening_scene = include_opening_transition and not at_times
    decode_indices = indices
    if check_opening_scene:
        scene_samples = {
            closest_frame(first + offset) for offset in OPENING_SCENE_SAMPLE_OFFSETS if first + offset < last
        }
        decode_indices = tuple(sorted(set(indices) | scene_samples))
    with Image.open(BytesIO(extract_desktop_first_frame(src))) as frame:
        display_size = frame.size
        scale = min(1.0, max_edge / max(frame.size))
        width, height = (max(2, round(value * scale / 2) * 2) for value in frame.size)
    selector = "+".join(f"eq(n\\,{index})" for index in decode_indices)
    decode_alpha = preserve_alpha or background is not None
    decoder_args = alpha_input_args(probe_video(src)) if decode_alpha else []
    pixel_format, mode, channels = ("rgba", "RGBA", 4) if decode_alpha else ("rgb24", "RGB", 3)
    raw = ffmpeg_stdout(
        [
            *decoder_args,
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"select={selector},scale={width}:{height},setsar=1,format={pixel_format}",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pixel_format,
            "-",
        ],
        failure_message="桌面视频采样检查失败",
    )
    frame_size = width * height * channels
    if len(raw) != frame_size * len(decode_indices):
        raise VideoProcessError("桌面视频首尾帧无法完整解码")
    frames: dict[int, bytes] = {}
    for offset, index in enumerate(decode_indices):
        data = raw[offset * frame_size : (offset + 1) * frame_size]
        with Image.frombytes(mode, (width, height), data) as frame, BytesIO() as output:
            if background is not None:
                with Image.new("RGB", frame.size, background) as composite:
                    composite.paste(frame, mask=frame.getchannel("A"))
                    composite.save(output, format="WEBP", lossless=True)
            else:
                frame.save(output, format="WEBP", lossless=True)
            frames[index] = output.getvalue()
    full_frames = tuple(frames[index] for index in indices)
    frame_times = tuple(times[index] - first for index in indices)
    details, transition, transient_times = (
        review_face_details(
            full_frames,
            times,
            source=src,
            frame_indices=indices,
            display_size=display_size,
            preserve_alpha=preserve_alpha,
            background=background,
            decoder_args=tuple(decoder_args),
            scan_timeline=not at_times,
        )
        if include_face_details
        else ((), None, ())
    )
    scene_transition = (
        opening_scene_evidence(
            tuple(frames[index] for index in decode_indices), tuple(times[index] - first for index in decode_indices)
        )
        if check_opening_scene
        else None
    )
    return DesktopVideoFrames(full_frames, frame_times, details, transition, scene_transition, transient_times)


@dataclass(frozen=True)
class FaceMotionComparison:
    image: bytes
    loss_times: tuple[float, ...]


def face_motion_comparison(source: Path, delivered: tuple[tuple[float, bytes], ...]) -> FaceMotionComparison | None:
    """按成片局部的实际同时刻并列原表演，避免把精修引入的动作当作角色选择。"""
    if len(delivered) < 3:
        return None
    original = sample_desktop_video_frames(
        source, include_face_details=True, at_times=tuple(time for time, _ in delivered)
    )
    pairs = [
        (time, original_pixels, pixels)
        for time, pixels in delivered
        for source_time, original_pixels in original.face_details
        if abs(time - source_time) <= 0.001
    ]
    if len(pairs) < 3:
        return None
    loss_times = face_structure_loss_times(pairs)
    if loss_times:
        selected = {pairs[0][0], pairs[-1][0], *loss_times}
        pairs = [row for row in pairs if row[0] in selected]
    sheets = compare_face_sequences({"M": [(t, m) for t, m, _ in pairs], "F": [(t, f) for t, _, f in pairs]})
    return FaceMotionComparison(sheets[0], loss_times) if sheets else None


def extract_desktop_first_frame(src: Path) -> bytes:
    """原分辨率解码实际首帧；FFmpeg 自动应用显示旋转，再按 SAR 还原显示比例。"""
    data = ffmpeg_stdout(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "scale=round(iw*max(sar\\,1)):round(ih/min(sar\\,1)),setsar=1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        failure_message="桌面视频首帧解码失败",
    )
    try:
        with Image.open(BytesIO(data)) as frame:
            frame.load()
            if frame.width < 1 or frame.height < 1:
                raise VideoProcessError("桌面视频首帧尺寸无效")
    except OSError as exc:
        raise VideoProcessError("桌面视频首帧解码失败") from exc
    return data


def _target_dimensions(aspect_ratio: str) -> tuple[int, int]:
    try:
        width, height = (int(value) for value in aspect_ratio.split(":"))
        if min(width, height) <= 0:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise VideoProcessError("桌面视频目标画幅无效") from exc
    return width, height


def _fit_filter(first_frame: bytes, aspect_ratio: str | None) -> tuple[str, tuple[int, int], bool]:
    with Image.open(BytesIO(first_frame)) as frame:
        width, height = frame.size
    if width * height > 3840 * 2160:
        raise VideoProcessError("桌面视频分辨率超出处理上限")
    target = _target_dimensions(aspect_ratio) if aspect_ratio else (width, height)
    try:
        crop_left, crop_top, crop_right, crop_bottom = centered_crop_box(width, height, *target, alignment=2)
    except ValueError as exc:
        raise VideoProcessError("桌面视频画幅差异超过少量裁切范围，请重新调整") from exc
    output_width, output_height = crop_right - crop_left, crop_bottom - crop_top
    return (
        f"scale={width}:{height},setsar=1,crop={output_width}:{output_height}:{crop_left}:{crop_top}:exact=1",
        (output_width, output_height),
        (output_width, output_height) != (width, height),
    )


def _desktop_result(dst: Path, expected_size: tuple[int, int], *, fitted: bool) -> DesktopVideoResult:
    output = probe_video(dst)
    if (output.width, output.height) != expected_size:
        raise VideoProcessError("桌面视频处理后的画幅无效")
    run_ffmpeg(["-xerror", "-i", str(dst), "-map", "0:v:0", "-an", "-f", "null", "-"], label="桌面视频解码检查")
    first_frame = extract_desktop_first_frame(dst)
    with Image.open(BytesIO(first_frame)) as frame, BytesIO() as buffer:
        frame.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        frame.save(buffer, format="WEBP", quality=90)
        cover = buffer.getvalue()
    return DesktopVideoResult(
        output.width, output.height, round(output.duration_seconds * 1000), cover, first_frame, fitted
    )


def prepare_desktop_video(src: Path, dst: Path, *, aspect_ratio: str | None = None) -> DesktopVideoResult:
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160 or probe.fps > 120:
        raise VideoProcessError("桌面视频分辨率或帧率超出处理上限")
    first_frame = extract_desktop_first_frame(src)
    video_filter, expected_size, fitted = _fit_filter(first_frame, aspect_ratio)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"{video_filter},fps=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        label="桌面视频处理",
    )
    return _desktop_result(dst, expected_size, fitted=fitted)
