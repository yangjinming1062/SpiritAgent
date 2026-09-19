"""标准化原图：固定外观基准的唯一来源。

原始文件只读，本模块产出规范化 RGBA（EXIF 方向、8-bit、sRGB 直通、不叠任何
肤色/风格调整）与像素哈希，供重建与验收作为逐像素基准使用。
"""

import hashlib
import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps

NORMALIZATION_VERSION = "srcnorm/1"


@dataclass(frozen=True)
class SourceAppearance:
    """外观基准。``canonical`` 为规范化后像素，宽高即原图方向修正后的尺寸。"""

    canonical: Image.Image
    source_sha256: str
    pixel_sha256: str
    alpha_mode: Literal["transparent", "opaque"]
    normalization_version: str = NORMALIZATION_VERSION


def load_source_appearance(data: bytes) -> SourceAppearance:
    """解码原始字节并生成规范化基准；解码失败抛 ValueError。"""
    digest = hashlib.sha256(data).hexdigest()
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        canonical = image.convert("RGBA")
    except Exception as exc:
        raise ValueError(f"source image is not decodable: {exc}") from exc

    alpha_mode = "transparent" if _has_usable_alpha(canonical) else "opaque"
    pixel_digest = hashlib.sha256(canonical.tobytes()).hexdigest()
    return SourceAppearance(
        canonical=canonical,
        source_sha256=digest,
        pixel_sha256=pixel_digest,
        alpha_mode=alpha_mode,
    )


def _has_usable_alpha(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    lo, _ = alpha.getextrema()
    return lo < 250
