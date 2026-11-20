"""从原视频的密集采样给出运动测量，完整画面继续用于判断动作和场地。"""

import hashlib
import math
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter, ImageOps

from infrastructure.logger import get_logger

from .desktop import sample_desktop_video_frames
from .face_detail import review_model_path, subject_horizontal_span
from .ffmpeg import video_frame_times

logger = get_logger(__name__)
_POSE_MODEL = "movenet-singlepose-lightning.onnx"
_POSE_SHA256 = "1ad4f8d6c2f776a9967db3993c9ca740bc350104f9d37c151dc183fc29a464ad"


@lru_cache(maxsize=1)
def _pose_session(path: Path, modified_ns: int) -> ort.InferenceSession | None:
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != _POSE_SHA256:
            logger.warning("Unexpected motion measurement weights")
            return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _ankle_sequence(frames: tuple[bytes, ...], times: tuple[float, ...]) -> dict | None:
    path = review_model_path(_POSE_MODEL)
    if not path.is_file():
        return None
    try:
        session = _pose_session(path, path.stat().st_mtime_ns)
        if session is None:
            return None
        heights = []
        for data in frames:
            with Image.open(BytesIO(data)) as frame:
                image = np.asarray(ImageOps.pad(frame.convert("RGB"), (192, 192)), dtype=np.int32)
            points = session.run(None, {"input": image[None]})[0][0, 0]
            shoulders = points[5, :2] - points[6, :2]
            # 大幅倾斜或接近侧身时，图像纵轴上的踝高低不足以核对换脚。
            frontal = abs(shoulders[1]) >= 0.08 and abs(shoulders[0]) <= 0.4 * abs(shoulders[1])
            confident = frontal and points[[5, 6, 11, 12, 13, 14, 15, 16], 2].min() >= 0.30
            torso = np.linalg.norm(points[[5, 6], :2].mean(axis=0) - points[[11, 12], :2].mean(axis=0))
            heights.append(float((points[15, 0] - points[16, 0]) / torso) if confident and torso >= 0.08 else None)
    except Exception:
        logger.warning("Video joint measurements unavailable", exc_info=True)
        return None
    reliable_fraction = sum(value is not None for value in heights) / len(times)
    if reliable_fraction < 0.75:
        return None
    # 踝部高度差按躯干长度归一化，超过 6.5% 且连续两帧一致才记入轨迹。
    changes = []
    last_side = observed_side = None
    for index, height in enumerate(heights):
        if height is None or abs(height) <= 0.065:
            observed_side = None
            continue
        side = "right" if height > 0 else "left"
        if side == observed_side and side != last_side:
            changes.append([round(times[index - 1], 2), side])
            last_side = side
        observed_side = side
    return {"time_and_higher_ankle": changes, "reliable_fraction": round(reliable_fraction, 2)}


def video_motion_profile(
    src: Path, duration_seconds: float, *, character: bool, full_frames: tuple[bytes, ...] = ()
) -> dict:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        times = video_frame_times(src)
        duration_seconds = times[-1] - times[0]
    samples = sample_desktop_video_frames(
        src, sample_count=max(2, 1 + round(min(48, duration_seconds * 5))), max_edge=320
    )
    if len(samples.frames) < 2:
        return {"duration_seconds": duration_seconds, "sample_count": len(samples.frames)}
    span = subject_horizontal_span(full_frames or samples.frames) if character else (0.0, 1.0)
    center = sum(span) / 2
    pixels = []
    for data in samples.frames:
        with Image.open(BytesIO(data)) as frame:
            pixels.append(np.asarray(frame.convert("L").filter(ImageFilter.GaussianBlur(1)), dtype=np.int16))
    # 平滑后在 8 位亮度上比较相邻采样，减小压缩噪声对分区变化量的影响。
    changes = np.abs(np.diff(np.stack(pixels), axis=0)) > 12
    _, height, width = changes.shape
    x1 = int(max(0, center - 0.15) * width)
    x2 = max(x1 + 1, round(min(1, center + 0.15) * width))
    edge = max(1, round(width * 0.14))
    regions = {
        "upper": changes[:, : max(1, round(height * 0.40)), x1:x2],
        "middle": changes[:, int(height * 0.35) : max(1, round(height * 0.85)), x1:x2],
        "lower": changes[:, int(height * 0.65) :, x1:x2],
        "sides": np.concatenate((changes[:, :, :edge], changes[:, :, -edge:]), axis=2),
    }
    bins = np.array_split(np.arange(len(changes)), min(8, len(changes)))
    result = {
        "duration_seconds": round(duration_seconds, 2),
        "sample_count": len(samples.frames),
        "window_end_seconds": [round(samples.times_seconds[window[-1] + 1], 2) for window in bins],
        "region_center_x_fraction": round(center, 2),
        "changed_pixel_percent": {
            name: [round(float(region[window].mean()) * 100, 1) for window in bins] for name, region in regions.items()
        },
    }
    dense = max(np.diff(samples.times_seconds)) <= 0.35
    if (
        character
        and span != (0.0, 1.0)
        and dense
        and (ankles := _ankle_sequence(samples.frames, samples.times_seconds))
    ):
        result["ankles"] = ankles
    return result


def repeated_steps_after_limit(profile: dict, requested: int) -> str | None:
    alternations = (profile.get("ankles") or {}).get("time_and_higher_ankle", [])
    # 起步与收势各可出现一次额外姿态。只有反复左右换边且延续到后半段才形成矛盾。
    if (
        len(alternations) < requested + 3
        or not profile.get("duration_seconds")
        or alternations[-1][0] < profile["duration_seconds"] * 0.60
    ):
        return None
    times = "、".join(f"{t:g}" for t, _ in alternations)
    return f"原片在 {times} 秒先后出现交替的左右踝高低关系，后半段仍在换脚，超出限定的 {requested} 步及收势。"
