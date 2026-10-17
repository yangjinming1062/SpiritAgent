"""图片与视频共用的居中裁切几何；尺寸取整最多容许两个像素。"""

import math

MAX_DESKTOP_CROP_FRACTION = 0.10


def crop_fraction(width: int, height: int, target_width: int, target_height: int) -> float:
    """铺满目标画幅所需裁掉的对应边长比例，不包含像素取整。"""
    if min(width, height, target_width, target_height) <= 0:
        raise ValueError("画幅尺寸必须为正数")
    relative = width * target_height / (height * target_width)
    return 1 - min(relative, 1 / relative)


def centered_crop_box(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    *,
    alignment: int = 1,
    max_crop_fraction: float = MAX_DESKTOP_CROP_FRACTION,
) -> tuple[int, int, int, int]:
    """返回 Pillow 风格裁切框；不放大、不拉伸，输出边长满足指定对齐。"""
    crop_fraction(width, height, target_width, target_height)
    if alignment < 1 or not 0 <= max_crop_fraction < 1:
        raise ValueError("裁切参数无效")
    ratio = target_width / target_height
    crop_width = min(width, height * ratio)
    crop_height = min(height, width / ratio)
    aligned_width = math.floor(crop_width / alignment + 1e-9) * alignment
    aligned_height = math.floor(crop_height / alignment + 1e-9) * alignment
    if aligned_width < 1 or aligned_height < 1:
        raise ValueError("裁切结果尺寸无效")
    if (
        width - aligned_width > width * max_crop_fraction + 2 + 1e-9
        or height - aligned_height > height * max_crop_fraction + 2 + 1e-9
    ):
        raise ValueError("画幅差异超过可裁切范围")
    left, top = (width - aligned_width) // 2, (height - aligned_height) // 2
    return left, top, left + aligned_width, top + aligned_height


def can_crop_to_aspect(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    *,
    alignment: int = 1,
    max_crop_fraction: float = MAX_DESKTOP_CROP_FRACTION,
) -> bool:
    """与最终裁切使用同一边界，供供应商画幅筛选和上传预检。"""
    try:
        centered_crop_box(
            width,
            height,
            target_width,
            target_height,
            alignment=alignment,
            max_crop_fraction=max_crop_fraction,
        )
    except ValueError:
        return False
    return True


def resolution_fit_key(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    *,
    keep_aspect: bool = False,
    output_aspect: tuple[int, int] | None = None,
    alignment: int = 1,
) -> tuple[float, int, float]:
    """按桌面内有效显示尺寸选原生档位，同等接近时优先较少像素和裁边。"""
    crop_fraction(width, height, target_width, target_height)
    aspect_width, aspect_height = output_aspect or ((width, height) if keep_aspect else (target_width, target_height))
    left, top, right, bottom = centered_crop_box(
        width,
        height,
        aspect_width,
        aspect_height,
        alignment=alignment,
    )
    effective_width, effective_height = right - left, bottom - top
    if keep_aspect:
        scale = min(target_width / aspect_width, target_height / aspect_height)
        expected_width, expected_height = aspect_width * scale, aspect_height * scale
    else:
        expected_width, expected_height = target_width, target_height
    error = max(abs(effective_width / expected_width - 1), abs(effective_height / expected_height - 1))
    loss = 1 - effective_width * effective_height / (width * height)
    return round(error, 12), width * height, round(loss, 12)
