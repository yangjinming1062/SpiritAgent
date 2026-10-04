"""动作图片与完整透明片段的画面验收；视频不裁切时间轴或改造循环接点。"""

from pathlib import Path

import numpy as np
from PIL import Image

from .ffmpeg import ActionMaterialRejectedError, VideoProbe, alpha_input_args, ffmpeg_stdout, probe_video


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
        raise ActionMaterialRejectedError("动作素材未解码出有效画面，请重新生成此动作")
    return raw.reshape(-1, height, width).astype(np.float32) / 255


def _check_common_gates(min_coverage: float, max_coverage: float, max_border_coverage: float) -> None:
    """通用机械门禁：主体存在、背景干净、不贴边。不证明动作语义或接点质量。"""
    if min_coverage < 0.025 or max_coverage > 0.85:
        raise ActionMaterialRejectedError("角色缺失或背景未去干净，请重新生成此动作")
    if max_coverage / min_coverage > 1.8:
        raise ActionMaterialRejectedError("角色轮廓变化过大，请重新生成此动作")
    if max_border_coverage > 0.04:
        raise ActionMaterialRejectedError("画面边缘仍有不透明内容，可能是角色贴边或背景残留。")


def validate_action_image(image: Image.Image) -> None:
    """单张图片与视频共用主体、背景和贴边门禁，保留独立的静态 alpha 校验。"""
    alpha_channel = image.getchannel("A")
    alpha_min, alpha_max = alpha_channel.getextrema()
    if alpha_min > 8 or alpha_max < 240:
        raise ActionMaterialRejectedError("动作图片缺少有效前景或透明背景")
    scale = min(96 / image.width, 1024 / image.height)
    width, height = max(2, round(image.width * scale)), max(2, round(image.height * scale))
    alpha = np.asarray(alpha_channel.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    coverage = float((alpha > 0.5).mean())
    border = np.concatenate((alpha[:2].ravel(), alpha[-2:].ravel(), alpha[:, :2].ravel(), alpha[:, -2:].ravel()))
    _check_common_gates(coverage, coverage, float((border > 0.5).mean()))


def validate_action_clip(src: Path) -> None:
    """循环与单次动作共用画面门禁，完整保留模型生成的时间轴。"""
    probe = probe_video(src)
    alpha = _decode_alpha_frames(src, probe)
    coverage = (alpha > 0.5).mean(axis=(1, 2))
    border = np.concatenate(
        (
            alpha[:, :2].reshape(len(alpha), -1),
            alpha[:, -2:].reshape(len(alpha), -1),
            alpha[:, :, :2].reshape(len(alpha), -1),
            alpha[:, :, -2:].reshape(len(alpha), -1),
        ),
        axis=1,
    )
    _check_common_gates(float(coverage.min()), float(coverage.max()), float((border > 0.5).mean(axis=1).max()))
