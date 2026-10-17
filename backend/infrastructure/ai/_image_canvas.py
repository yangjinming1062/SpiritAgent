"""场景按交付尺寸选择供应商支持的画幅；结果随图片任务冻结。"""

import math
from dataclasses import dataclass

from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.media_geometry import MAX_DESKTOP_CROP_FRACTION, resolution_fit_key


@dataclass(frozen=True)
class ImageCanvas:
    size: str
    aspect_ratio: str | None = None
    resolution: str | None = None
    exact_size: bool = False
    native_width: int | None = None
    native_height: int | None = None


_GEMINI_1K: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "2:3": (848, 1264),
    "3:2": (1264, 848),
    "3:4": (896, 1200),
    "4:3": (1200, 896),
    "4:5": (928, 1152),
    "5:4": (1152, 928),
    "9:16": (768, 1376),
    "16:9": (1376, 768),
    "21:9": (1584, 672),
}
_GEMINI_25: dict[str, tuple[int, int]] = {
    **_GEMINI_1K,
    "2:3": (832, 1248),
    "3:2": (1248, 832),
    "3:4": (864, 1184),
    "4:3": (1184, 864),
    "4:5": (896, 1152),
    "5:4": (1152, 896),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "21:9": (1536, 672),
}
_GEMINI_FLASH_EXTRA: dict[str, tuple[int, int]] = {
    "1:4": (512, 2048),
    "4:1": (2048, 512),
    "1:8": (384, 3072),
    "8:1": (3072, 384),
}
_GROK_ASPECTS = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "21:9",
    "5:2",
)
_MINIMAX_FIXED: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "4:3": (1152, 864),
    "3:2": (1248, 832),
    "2:3": (832, 1248),
    "3:4": (864, 1152),
    "9:16": (720, 1280),
    "21:9": (1344, 576),
}
_QWEN_FIXED: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
    "3:2": (1152, 768),
    "2:3": (768, 1152),
    "21:9": (1512, 648),
}


def _ratio(label: str) -> float:
    width, height = label.split(":", 1)
    return float(width) / float(height)


def _nearest_ratio(width: int, height: int, choices: tuple[str, ...]) -> str:
    target = width / height
    return min(choices, key=lambda label: abs(math.log(_ratio(label) / target)))


def _pixel_canvas(
    width: int,
    height: int,
    *,
    step: int,
    min_area: int,
    max_area: int = 2048**2,
    max_ratio: float = 8,
    min_edge: int = 0,
    max_edge: int | None = None,
    nearest: bool = False,
) -> ImageCanvas:
    if nearest:
        best: tuple[tuple[float, int, float], int, int] | None = None
        lower_edge = math.ceil(max(step, min_edge) / step) * step
        upper_edge = min(max_edge or max_area // lower_edge, math.floor(math.sqrt(max_area * max_ratio)))
        ratio = width / height
        for candidate_width in range(lower_edge, upper_edge + 1, step):
            lower_height = max(lower_edge, math.ceil(min_area / candidate_width / step) * step)
            lower_height = max(lower_height, math.ceil(candidate_width / max_ratio / step) * step)
            upper_height = min(
                upper_edge,
                math.floor(max_area / candidate_width / step) * step,
                math.floor(candidate_width * max_ratio / step) * step,
            )
            # 固定宽度时，误差只在目标高、等比高度或合法边界附近取得最小值。
            crop_lower = (candidate_width * (1 - MAX_DESKTOP_CROP_FRACTION) - 2) / ratio
            crop_upper = (candidate_width / ratio + 2) / (1 - MAX_DESKTOP_CROP_FRACTION)
            boundaries = (lower_height, upper_height, height, candidate_width / ratio, crop_lower, crop_upper)
            heights = {
                math.floor(boundary / step) * step + delta * step for boundary in boundaries for delta in (-1, 0, 1, 2)
            }
            for candidate_height in sorted(heights):
                if not lower_height <= candidate_height <= upper_height:
                    continue
                try:
                    key = resolution_fit_key(candidate_width, candidate_height, width, height)
                except ValueError:
                    continue
                if best is None or key < best[0]:
                    best = key, candidate_width, candidate_height
        if best is None:
            raise ValueError("图片供应商没有可在少量裁边后适配目标的原生画幅")
        _key, selected_width, selected_height = best
        return ImageCanvas(
            size=f"{selected_width}x{selected_height}",
            exact_size=True,
            native_width=selected_width,
            native_height=selected_height,
        )
    ratio = max(1 / max_ratio, min(max_ratio, width / height))
    # 比例受限时先取能覆盖目标的画布，之后才按像素/边长上限等比缩小。
    base_width = max(width, height * ratio)
    base_height = base_width / ratio
    scale = max(1.0, math.sqrt(min_area / (base_width * base_height)))
    if min_edge:
        scale = max(scale, min_edge / min(base_width, base_height))
    limit = math.sqrt(max_area / (base_width * base_height))
    if max_edge:
        limit = min(limit, max_edge / max(base_width, base_height))
    scale = min(scale, limit)
    rounding = math.ceil if scale >= 1 else round
    selected_width = max(step, rounding(base_width * scale / step) * step)
    selected_height = max(step, rounding(base_height * scale / step) * step)
    if min_edge:
        selected_width, selected_height = max(min_edge, selected_width), max(min_edge, selected_height)
    if max_edge:
        selected_width, selected_height = min(max_edge, selected_width), min(max_edge, selected_height)
    while selected_width * selected_height > max_area:
        if selected_width / selected_height > ratio:
            selected_width -= step
        else:
            selected_height -= step
    # 量化不能越过模型允许的极端比例。
    if selected_width / selected_height > max_ratio:
        selected_width = math.floor(selected_height * max_ratio / step) * step
    elif selected_height / selected_width > max_ratio:
        selected_height = math.floor(selected_width * max_ratio / step) * step
    return ImageCanvas(
        size=f"{selected_width}x{selected_height}",
        exact_size=True,
        native_width=selected_width,
        native_height=selected_height,
    )


def _closest_canvas(candidates: list[ImageCanvas], width: int, height: int) -> ImageCanvas:
    ranked: list[tuple[tuple[float, int, float], ImageCanvas]] = []
    for canvas in candidates:
        if canvas.native_width is None or canvas.native_height is None:
            raise ValueError("图片档位缺少原生尺寸规格")
        try:
            key = resolution_fit_key(canvas.native_width, canvas.native_height, width, height)
        except ValueError:
            continue
        ranked.append((key, canvas))
    if not ranked:
        raise ValueError("图片供应商没有可在少量裁边后适配目标的原生画幅")
    return min(ranked, key=lambda candidate: candidate[0])[1]


def select_image_canvas(config: ProviderConfig, width: int, height: int, *, nearest: bool = False) -> ImageCanvas:
    """默认选够用的最小原生档；桌景按裁切后的尺寸选择最接近目标的档位。"""
    if width <= 0 or height <= 0:
        raise ValueError("图片目标尺寸必须为正整数")
    provider, model = config.provider_name, config.model.lower()
    if provider == "qwen":
        if model.startswith(("qwen-image-2.", "qwen-image-3.")):
            return _pixel_canvas(width, height, step=8, min_area=512**2, nearest=nearest)
        if nearest:
            return _closest_canvas(
                [
                    ImageCanvas(size=f"{w}x{h}", exact_size=True, native_width=w, native_height=h)
                    for w, h in _QWEN_FIXED.values()
                ],
                width,
                height,
            )
        aspect = _nearest_ratio(width, height, tuple(_QWEN_FIXED))
        w, h = _QWEN_FIXED[aspect]
        return ImageCanvas(size=f"{w}x{h}", exact_size=True, native_width=w, native_height=h)
    if provider == "minimax":
        if model == "image-01":
            return _pixel_canvas(
                width,
                height,
                step=8,
                min_area=512**2,
                max_ratio=4,
                min_edge=512,
                max_edge=2048,
                nearest=nearest,
            )
        if nearest:
            return _closest_canvas(
                [
                    ImageCanvas(size=aspect, aspect_ratio=aspect, native_width=w, native_height=h)
                    for aspect, (w, h) in _MINIMAX_FIXED.items()
                ],
                width,
                height,
            )
        aspect = _nearest_ratio(width, height, tuple(_MINIMAX_FIXED))
        w, h = _MINIMAX_FIXED[aspect]
        return ImageCanvas(size=aspect, aspect_ratio=aspect, native_width=w, native_height=h)
    if provider == "local":
        return _pixel_canvas(width, height, step=32, min_area=512**2, nearest=nearest)
    if provider == "gemini":
        choices = dict(_GEMINI_25 if model.startswith("gemini-2.5-") else _GEMINI_1K)
        if model.startswith("gemini-3.1-flash-image"):
            choices.update(_GEMINI_FLASH_EXTRA)
        aspect = _nearest_ratio(width, height, tuple(choices))
        w, h = choices[aspect]
        high_resolution = model.startswith(("gemini-3-pro-image", "gemini-3.1-pro-image", "gemini-3.1-flash-image"))
        scales = (1, 2, 4) if high_resolution else (1,)
        if nearest:
            return _closest_canvas(
                [
                    ImageCanvas(
                        size=f"{w * scale}x{h * scale}",
                        aspect_ratio=label,
                        resolution=f"{scale}K",
                        native_width=w * scale,
                        native_height=h * scale,
                    )
                    for label, (w, h) in choices.items()
                    for scale in scales
                ],
                width,
                height,
            )
        scale = next((scale for scale in scales if w * scale >= width and h * scale >= height), scales[-1])
        return ImageCanvas(
            size=f"{w * scale}x{h * scale}",
            aspect_ratio=aspect,
            resolution=f"{scale}K",
            native_width=w * scale,
            native_height=h * scale,
        )
    if provider == "grok":
        aspect = _nearest_ratio(width, height, _GROK_ASPECTS)
        if nearest:
            # 官方按 1024² / 2048² 标称档位；非方形像素仅供规划，不写入成品实际尺寸。
            return _closest_canvas(
                [
                    ImageCanvas(
                        size=label,
                        aspect_ratio=label,
                        resolution=f"{scale}k",
                        native_width=round(1024 * scale * math.sqrt(_ratio(label))),
                        native_height=round(1024 * scale / math.sqrt(_ratio(label))),
                    )
                    for label in _GROK_ASPECTS
                    for scale in (1, 2)
                ],
                width,
                height,
            )
        # xAI 2k 不承诺固定像素串；画幅门禁使用冻结的比例，实际尺寸从成品读取。
        return ImageCanvas(size=aspect, aspect_ratio=aspect, resolution="2k")
    raise ValueError(f"未定义图片供应商画幅能力：{provider}")
