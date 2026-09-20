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


def select_loop(src: Path, *, max_seconds: int = 2) -> LoopWindow:
    """在一秒到指定上限之间比较前景与接点速度；支持恰好一秒的完整素材。"""
    if not 1 <= max_seconds <= 12:
        raise VideoProcessError("循环时长上限无效")
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
        for j in range(i + 24, min(len(frames), i + max_seconds * 24) + 1):
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
