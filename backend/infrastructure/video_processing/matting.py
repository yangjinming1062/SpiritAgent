"""角色静态图片透明化：细化 alpha 与前景颜色。"""

import math
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageFilter

from infrastructure.config import SETTINGS
from infrastructure.video_processing.ffmpeg import (
    VideoProcessError,
    VideoToolUnavailableError,
)

_IMAGE_MATTING_MAX_PIXELS = 1_000_000


def require_matting_model() -> Path:
    path = Path(SETTINGS.data_dir) / "models" / f"{SETTINGS.matting_model}.onnx"
    if not path.is_file():
        raise VideoToolUnavailableError("抠像模型未就绪，请配置后再生成图片或动作", internal=f"missing {path}")
    return path


class ForegroundMatte:
    """ISNet 只确定前景，不以颜色距离删除皮肤、衣物或毛发。"""

    def __init__(self, model_path: Path) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def apply(self, image: Image.Image) -> Image.Image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        resized = image.convert("RGB").resize((1024, 1024), Image.Resampling.BILINEAR)
        tensor = (np.asarray(resized, dtype=np.float32) / 255.0 - 0.5).transpose(2, 0, 1)[None]
        mask = np.squeeze(self.session.run(None, {self.input_name: tensor})[0]).astype(np.float32)
        if mask.ndim != 2 or not np.isfinite(mask).all():
            raise VideoProcessError("抠像模型返回无效遮罩")
        # 不逐帧 min/max 拉伸，否则每帧概率标尺变化会放大边缘闪烁。
        alpha = np.asarray(Image.fromarray(np.clip(mask, 0, 1)).resize(image.size, Image.Resampling.BILINEAR))
        alpha = np.clip((alpha - 0.02) / 0.96, 0, 1)
        # 只在均匀背景且语义边缘半透明时去背景混色；实体 RGB 不改动。
        border = np.concatenate(
            (rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3), rgb[:, -3:].reshape(-1, 3)),
        )
        background = np.median(border, axis=0)
        if float(np.quantile(np.abs(border - background), 0.9)) < 0.035:
            edge = (alpha > 0.08) & (alpha < 0.98)
            foreground = (rgb - (1 - alpha[..., None]) * background) / np.maximum(alpha[..., None], 0.08)
            rgb = np.where(edge[..., None], np.clip(foreground, 0, 1), rgb)
        rgba = np.concatenate((rgb, alpha[..., None]), axis=2)
        return Image.fromarray(np.round(rgba * 255).astype(np.uint8))


def _refine_image_edges(image: Image.Image, segmented: Image.Image) -> Image.Image:
    """分割只提供已知区域；静态图边缘另估计覆盖率与未混入背景的前景颜色。"""
    # 首次导入会编译数值内核，只在图片处理线程中加载，避免阻塞服务启动。
    from pymatting import estimate_alpha_cf, estimate_foreground_ml

    mask = segmented.getchannel("A")
    bounds = mask.point(lambda value: 255 if value > 5 else 0).getbbox()
    if bounds is None:
        raise VideoProcessError("图片抠像未找到可见前景")
    padding = max(3, math.ceil(max(image.size) / 200)) + 3
    left, top, right, bottom = bounds
    bounds = (
        max(0, left - padding),
        max(0, top - padding),
        min(image.width, right + padding),
        min(image.height, bottom + padding),
    )
    source = image.convert("RGB").crop(bounds)
    region_mask = mask.crop(bounds)
    scale = min(1.0, math.sqrt(_IMAGE_MATTING_MAX_PIXELS / (source.width * source.height)))
    size = (int(source.width * scale), int(source.height * scale))
    if min(size) < 3:
        raise VideoProcessError("角色图片过于细长或尺寸过小，无法可靠处理透明边缘")
    work = source.resize(size, Image.Resampling.LANCZOS) if size != source.size else source
    work_mask = region_mask.resize(size, Image.Resampling.BILINEAR)
    radius = max(1, round(max(size) / 200))
    erosion = ImageFilter.MinFilter(2 * radius + 1)
    # 收缩确定前景，把漏入的背景和抗锯齿发丝留给像素估计。
    foreground = np.asarray(work_mask.point(lambda value: 255 if value >= 250 else 0).filter(erosion)) > 0
    # 确定背景不收缩，保留肢体、把手及发丝之间的细窄空隙。
    background = np.asarray(work_mask) <= 5
    if not foreground.any() or not background.any():
        raise VideoProcessError("图片透明边缘缺少可靠前景或背景，请更换清晰完整的图片")
    trimap = np.full((size[1], size[0]), 0.5, dtype=np.float64)
    trimap[foreground], trimap[background] = 1.0, 0.0
    try:
        alpha = estimate_alpha_cf(
            np.asarray(work, dtype=np.float64) / 255,
            trimap,
            cg_kwargs={"maxiter": 2000},
        )
        if alpha.shape != trimap.shape or not np.isfinite(alpha).all():
            raise ValueError("invalid image alpha")
        alpha = np.asarray(
            Image.fromarray(alpha.astype(np.float32)).resize(source.size, Image.Resampling.BILINEAR),
        ).clip(0, 1)
        original = np.asarray(source, dtype=np.float32) / 255
        colors = estimate_foreground_ml(original, alpha)
        if colors.shape != original.shape or not np.isfinite(colors).all():
            raise ValueError("invalid image foreground")
    except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
        raise VideoProcessError("图片透明边缘处理失败，请重试已有图片", internal=str(exc)) from exc
    # 不对确定的不透明主体做颜色重建，避免改变白色衣物、道具及实体细节。
    colors[alpha >= 1 - 1e-6] = original[alpha >= 1 - 1e-6]
    colors[alpha == 0] = 0
    rgba = np.concatenate((np.clip(colors, 0, 1), alpha[..., None]), axis=2)
    result = segmented.copy()
    result.paste(Image.fromarray(np.round(rgba * 255).astype(np.uint8)), bounds[:2])
    return result


def matte_image(image: Image.Image, model_path: Path) -> Image.Image:
    """静态图片以 ISNet 建立三分图，再求解 alpha 与前景颜色；保留原画布。"""
    return _refine_image_edges(image, ForegroundMatte(model_path).apply(image))
