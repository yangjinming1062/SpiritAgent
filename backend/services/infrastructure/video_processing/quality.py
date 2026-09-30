"""单动作透明片段验收与循环接点选择；不推测或切分不同动作的语义。"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from modules.companion import ABSOLUTE_MAX_DURATION_SECONDS

from .ffmpeg import VideoProbe, VideoProcessError, _binary, _run, alpha_input_args, probe_video
from .limits import SOURCE_DURATION_TOLERANCE_SECONDS


@dataclass(frozen=True)
class ClipWindow:
    start: float
    end: float


def _decode_alpha_frames(src: Path, probe: VideoProbe, width: int = 96) -> np.ndarray:
    """解码为 24fps 小尺寸 RGBA 帧数组；不足一秒时拒绝。"""
    height = max(2, round(probe.height * width / probe.width))
    proc = _run(
        [
            _binary("ffmpeg"),
            "-v",
            "error",
            *alpha_input_args(probe),
            "-i",
            str(src),
            "-vf",
            f"fps=24,scale={width}:{height},format=rgba",
            "-f",
            "rawvideo",
            "-",
        ],
    )
    if proc.returncode:
        raise VideoProcessError("透明片段验收解码失败", internal=proc.stderr.decode(errors="replace")[:1000])
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    size = width * height * 4
    if len(raw) % size or len(raw) // size < 24:
        raise VideoProcessError("可用动作不足一秒，请重新生成此动作")
    return raw.reshape(-1, height, width, 4).astype(np.float32) / 255


def _check_common_gates(alpha: np.ndarray) -> None:
    """通用机械门禁：主体存在、背景干净、不贴边。不证明动作语义或接点质量。"""
    coverage = (alpha > 0.5).mean(axis=(1, 2))
    if coverage.min() < 0.025 or coverage.max() > 0.85:
        raise VideoProcessError("角色缺失或背景未去干净，请重新生成此动作")
    if coverage.max() / coverage.min() > 1.8:
        raise VideoProcessError("角色轮廓变化过大，请重新生成此动作")
    border = np.concatenate(
        (
            alpha[:, :2].reshape(len(alpha), -1),
            alpha[:, -2:].reshape(len(alpha), -1),
            alpha[:, :, :2].reshape(len(alpha), -1),
            alpha[:, :, -2:].reshape(len(alpha), -1),
        ),
        axis=1,
    )
    if np.max((border > 0.5).mean(axis=1)) > 0.04:
        raise VideoProcessError("画面边缘仍有不透明内容，可能是角色贴边或背景残留。")


def select_loop(src: Path, *, max_seconds: float) -> ClipWindow:
    """在一秒到指定上限之间比较前景与接点速度；支持恰好一秒的完整素材。

    max_seconds 向上取整到帧边界，避免浮点时长（如 1.5s）截断合法候选区间。
    """
    if not 1 <= max_seconds <= ABSOLUTE_MAX_DURATION_SECONDS:
        raise VideoProcessError("循环时长上限无效")
    max_frames = int(np.ceil(max_seconds * 24))
    probe = probe_video(src)
    if probe.duration_seconds > max_seconds + SOURCE_DURATION_TOLERANCE_SECONDS:
        raise VideoProcessError("动作素材超出设计时长，不予发布")
    frames = _decode_alpha_frames(src, probe)
    alpha = frames[..., 3]
    _check_common_gates(alpha)
    frames[..., :3] *= alpha[..., None]
    best: tuple[float, int, int] | None = None
    # 区间 [i,j)：有下一帧比较该端点，到素材末尾比较最后实帧；不要求额外端点帧，否则一秒 24 帧会被误判为不足一秒。
    for i in range(len(frames) - 24 + 1):
        for j in range(i + 24, min(len(frames), i + max_frames) + 1):
            endpoint = min(j, len(frames) - 1)
            union = np.maximum(alpha[i], alpha[endpoint]) > 0.05
            if not union.any():
                continue
            appearance = float(np.abs(frames[i] - frames[endpoint])[union].mean())
            velocity = float(
                np.abs((frames[i + 1] - frames[i]) - (frames[endpoint] - frames[endpoint - 1]))[union].mean(),
            )
            score = appearance + velocity * 0.5
            if best is None or score < best[0]:
                best = (score, i, j)
    if best is None or best[0] > 0.065:
        raise VideoProcessError("动作首尾差异过大，无法自然循环，请重新生成此动作")
    _score, start, end = best
    return ClipWindow(start / 24, end / 24)


def select_full_clip(src: Path, *, max_seconds: float) -> ClipWindow:
    """once 动作：保留完整时间轴（准备、主体、结束），不测首尾接点、不因不闭环失败。

    超过设计上限的素材截到上限（仅允许小范围编码尾差修正量）；实际时长供回写。
    """
    if not 1 <= max_seconds <= ABSOLUTE_MAX_DURATION_SECONDS:
        raise VideoProcessError("动作时长上限无效")
    probe = probe_video(src)
    if probe.duration_seconds > max_seconds + SOURCE_DURATION_TOLERANCE_SECONDS:
        raise VideoProcessError("动作素材超出设计时长，不予发布")
    frames = _decode_alpha_frames(src, probe)
    _check_common_gates(frames[..., 3])
    return ClipWindow(0.0, min(len(frames) / 24, max_seconds))
