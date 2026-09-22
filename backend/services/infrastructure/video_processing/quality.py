"""单动作透明片段验收与循环接点选择；不推测或切分不同动作的语义。"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ffmpeg import VideoProcessError, _binary, _run, alpha_input_args, probe_video


@dataclass(frozen=True)
class LoopWindow:
    start: float
    end: float
    seam_error: float
    min_coverage: float
    max_coverage: float


def select_loop(src: Path, *, max_seconds: float = 2) -> LoopWindow:
    """在一秒到指定上限之间比较前景与接点速度；支持恰好一秒的完整素材。

    max_seconds 向上取整到帧边界，避免浮点时长（如 1.5s）截断合法候选区间。
    """
    if not 1 <= max_seconds <= 12:
        raise VideoProcessError("循环时长上限无效")
    max_frames = int(np.ceil(max_seconds * 24))
    probe = probe_video(src)
    if probe.duration_seconds > 12:
        raise VideoProcessError("动作素材过长")
    width = 96
    height = max(2, round(probe.height * width / probe.width))
    proc = _run(
        [
            _binary("ffmpeg"),
            "-v",
            "error",
            *alpha_input_args(src),
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
    frames = raw.reshape(-1, height, width, 4).astype(np.float32) / 255
    alpha = frames[..., 3]
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
        raise VideoProcessError("角色贴边裁切或背景残留，请重新生成此动作")
    frames[..., :3] *= alpha[..., None]
    best: tuple[float, int, int] | None = None
    # 区间为 [i,j)。存在下一帧时比较该端点；到素材末尾时比较最后实帧，
    # 不要求额外端点帧，否则标准的一秒 24 帧视频会被误判为不足一秒。
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
    score, start, end = best
    return LoopWindow(start / 24, end / 24, score, float(coverage.min()), float(coverage.max()))


@dataclass(frozen=True)
class FullClipWindow:
    start: float
    end: float
    min_coverage: float
    max_coverage: float


def _decode_alpha_frames(src: Path, width: int = 96) -> np.ndarray:
    """解码为小尺寸 RGBA 帧数组；供循环接点与完整片段共用。"""
    probe = probe_video(src)
    height = max(2, round(probe.height * width / probe.width))
    proc = _run(
        [
            _binary("ffmpeg"),
            "-v",
            "error",
            *alpha_input_args(src),
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
        raise VideoProcessError("角色贴边裁切或背景残留，请重新生成此动作")


def select_full_clip(src: Path, *, max_seconds: float = 10) -> FullClipWindow:
    """once 动作：保留完整时间轴（准备、主体、结束），不测首尾接点、不因不闭环失败。

    超过设计上限的素材截到上限（仅允许小范围编码尾差修正量）；实际时长供回写。
    """
    probe = probe_video(src)
    if probe.duration_seconds > max_seconds + 1.5:
        raise VideoProcessError("动作素材超出设计时长，不予发布")
    frames = _decode_alpha_frames(src)
    alpha = frames[..., 3]
    _check_common_gates(alpha)
    coverage = (alpha > 0.5).mean(axis=(1, 2))
    end = min(len(frames) / 24, max_seconds)
    return FullClipWindow(0.0, end, float(coverage.min()), float(coverage.max()))
