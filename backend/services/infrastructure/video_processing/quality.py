"""生成动作的完整透明片段验收；不裁切时间轴或改造循环接点。"""

from pathlib import Path

import numpy as np

from .ffmpeg import VideoProbe, VideoProcessError, alpha_input_args, ffmpeg_stdout, probe_video


def _decode_alpha_frames(src: Path, probe: VideoProbe, width: int = 96) -> np.ndarray:
    """解码为 24fps 小尺寸 alpha 帧数组。"""
    height = max(2, round(probe.height * width / probe.width))
    frames = ffmpeg_stdout(
        [
            *alpha_input_args(probe),
            "-i",
            str(src),
            "-vf",
            f"fps=24,scale={width}:{height},format=rgba,alphaextract",
            "-f",
            "rawvideo",
            "-",
        ],
        failure_message="透明片段验收解码失败",
    )
    raw = np.frombuffer(frames, dtype=np.uint8)
    size = width * height
    if not len(raw) or len(raw) % size:
        raise VideoProcessError("动作素材未解码出有效画面，请重新生成此动作")
    return raw.reshape(-1, height, width).astype(np.float32) / 255


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


def validate_action_clip(src: Path) -> None:
    """循环与单次动作共用画面门禁，完整保留模型生成的时间轴。"""
    probe = probe_video(src)
    _check_common_gates(_decode_alpha_frames(src, probe))
