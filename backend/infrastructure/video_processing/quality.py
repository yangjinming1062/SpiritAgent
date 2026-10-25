"""动作图片与完整透明片段的画面验收；视频不裁切时间轴或改造循环接点。"""

import numpy as np
from PIL import Image

from infrastructure.video_processing.ffmpeg import (
    ActionMaterialRejectedError,
)

# 画面门禁阈值：主体覆盖率带与边缘残留上限，图片与视频共用。
_MIN_SUBJECT_COVERAGE = 0.025
_MAX_SUBJECT_COVERAGE = 0.85
_MAX_BORDER_COVERAGE = 0.04


def validate_transparent_image(image: Image.Image) -> None:
    """真实透明角色图：可见前景、足量透明背景及干净边缘。"""
    alpha_channel = image.getchannel("A")
    alpha_min, alpha_max = alpha_channel.getextrema()
    if alpha_min > 8 or alpha_max < 128:
        raise ActionMaterialRejectedError("图片缺少有效前景或透明背景")
    scale = min(96 / image.width, 1024 / image.height)
    width, height = max(2, round(image.width * scale)), max(2, round(image.height * scale))
    alpha = np.asarray(alpha_channel.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    if float((alpha <= 8 / 255).mean()) < 0.15:
        raise ActionMaterialRejectedError("图片缺少足够的透明背景")
    coverage = float((alpha > 0.5).mean())
    border = np.concatenate((alpha[:2].ravel(), alpha[-2:].ravel(), alpha[:, :2].ravel(), alpha[:, -2:].ravel()))
    if not _MIN_SUBJECT_COVERAGE <= coverage <= _MAX_SUBJECT_COVERAGE:
        raise ActionMaterialRejectedError("图片缺少可见角色或背景未去干净")
    if float((border > 0.5).mean()) > _MAX_BORDER_COVERAGE:
        raise ActionMaterialRejectedError("图片边缘仍有不透明内容，可能是角色贴边或背景残留")
