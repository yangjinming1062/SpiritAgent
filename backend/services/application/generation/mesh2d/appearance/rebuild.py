"""原图锁定分层重建（source-locked rebuild）。

see-through 输出仅是候选分层：每个图层的可见区域用标准化原图像素回填，
生成内容只保留在原图中被更高运行时图层遮挡的区域。客户端 Rigger 按图层
数组顺序绘制（后画在上），且对头部五官做 HEAD_FEATURE_ORDER 槽位重排，
并对每层 alpha 做 cleanAlpha 去噪——遮挡归属与可见口径都必须镜像该运行时
行为，而不是 PSD 面板顺序或原始 alpha。

输出 PSD 保持扁平像素图层、原层名、原画布尺寸，客户端 /1 描述符无需变化。
"""

import io
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageFilter
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

from .source_asset import SourceAppearance

# 归属判定中“该图层在此处存在（会遮挡下层）”的有效 Alpha 下限。
_CLAIM_ALPHA = 8
# 门禁评测的内部前景收缩带：轮廓羽化与抠图边界单独口径，不参与逐像素比较。
_EDGE_BAND = 2
# 对齐搜索范围与判定：尺度以多初值为中心精调，平移 ±4px；低于该 IoU 判定对齐失败。
_ALIGN_IOU_MIN = 0.90
# 对齐剪影只采用角色语义层；近满幅层视为背景并入/填充，不参与包围盒。
_ALIGN_LAYER_BASE_NAMES = frozenset(
    {
        "body",
        "skin",
        "legs",
        "leg",
        "arms",
        "arm",
        "torso",
        "neck",
        "leg-upper",
        "leg-lower",
        "foot",
        "hand",
        "ears",
        "legwear",
        "bottomwear",
        "topwear",
        "footwear",
        "handwear",
        "earwear",
        "headwear",
        "face",
        "facedetail",
        "mouth_open",
        "mouth_close",
        "eyewhite",
        "irides",
        "eyelash",
        "eye_close",
        "nose",
        "eyebrow",
        "front hair",
        "back hair",
    },
)
_ALIGN_MAX_LAYER_COVERAGE = 0.85
_ALIGN_SCALE_DELTAS = (-0.10, -0.06, -0.03, 0.0, 0.03, 0.06, 0.10)
_ALIGN_SHIFT_STEPS = (-4.0, -2.0, 0.0, 2.0, 4.0)

# 客户端 buildRig 对每层 alpha 先做 cleanAlpha：alpha > 16 的 4 连通分量保留
# ≥40px 者，向外扩 3px，之外的像素运行时清零；没有任何 ≥40px 分量的层原样
# 返回不清洗。归属必须用同一口径，否则顶层 speckle 噪声会在重建时抢占可见
# 像素、又在运行时被客户端删掉，让下层生成内容透出顶替原图。
_RUNTIME_LABEL_ALPHA = 16
_RUNTIME_MIN_COMPONENT_PX = 40
_RUNTIME_CLEAN_DILATE_PX = 3


class AppearanceError(RuntimeError):
    """外观链失败。code 供调用方与诊断报告区分阶段。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LayerStat:
    name: str
    owned_pixels: int  # 回填原图像素数
    kept_pixels: int  # 保留生成像素数（遮挡补全区）


@dataclass(frozen=True)
class AffineTransform:
    """源图坐标 → PSD 画布坐标的等比映射：canvas = src * scale + offset。"""

    scale: float
    offset_x: float
    offset_y: float

    def to_pil_coeffs(self) -> tuple[float, float, float, float, float, float]:
        """PIL AFFINE 需要“输出→输入”系数。"""
        s = self.scale
        return (1.0 / s, 0.0, -self.offset_x / s, 0.0, 1.0 / s, -self.offset_y / s)


@dataclass
class RebuiltAppearance:
    psd_bytes: bytes
    canvas: tuple[int, int]
    transform: AffineTransform
    layer_stats: list[LayerStat] = field(default_factory=list)
    layer_names_bottom_to_top: list[str] = field(default_factory=list)


_HEAD_FEATURE_ORDER = {
    "face": 1,
    "facedetail": 2,
    "mouth_open": 3,
    "mouth_close": 3,
    "eyewhite": 4,
    "irides": 5,
    "eyelash": 6,
    "eye_close": 6,
    "nose": 7,
    "eyebrow": 8,
    "front hair": 9,
}

# rigger.js baseName 后缀剥离后的整名归一。
_BASE_NAME_ALIASES = {
    "fronthair": "front hair",
    "front_hair": "front hair",
    "backhair": "back hair",
    "back_hair": "back hair",
    "bottom_wear": "bottomwear",
    "bottom wear": "bottomwear",
    "leg_wear": "legwear",
    "leg wear": "legwear",
    "top_wear": "topwear",
    "top wear": "topwear",
    "hand_wear": "handwear",
    "hand wear": "handwear",
    "foot_wear": "footwear",
    "foot wear": "footwear",
    "shoes": "footwear",
    "boots": "footwear",
    "ear_wear": "earwear",
    "ear wear": "earwear",
    "head_wear": "headwear",
    "head wear": "headwear",
    "face_detail": "facedetail",
    "face detail": "facedetail",
}

# rigger.js normName 在 baseName 别名之外先做的整名归一（含 mouth 家族）。
_NORM_NAME_ALIASES = {
    "eyelash_c": "eye_close",
    "eyelash-c": "eye_close",
    "mouth_c": "mouth_close",
    "mouth-c": "mouth_close",
    "レイヤー 1": "facedetail",
    **_BASE_NAME_ALIASES,
}


def normalize_name(name: str) -> str:
    """镜像 normName；Rigger 的 face 锚点按此整名查找，不剥离左右后缀。"""
    raw = unicodedata.normalize("NFKC", name).strip().lower()
    raw = re.sub(r" のコピー\s*\d*$", "", raw)
    if raw in _NORM_NAME_ALIASES:
        raw = _NORM_NAME_ALIASES[raw]
    elif re.fullmatch(r"mouth(?:[ _-]?\d+)?", raw):
        raw = "mouth_open"
    return raw


def base_name(name: str) -> str:
    """镜像 normName → baseName：整名归一后剥离左右后缀与序号。

    顺序不能合并，例如 eyelash_c_2 剥离后保持 eyelash_c，不进入头部槽位。
    """
    raw = normalize_name(name)
    prev = ""
    while raw != prev:
        prev = raw
        raw = re.sub(r"[-_][lr]$", "", raw)
        raw = re.sub(r"_\d+$", "", raw)
    raw = raw.strip()
    return _BASE_NAME_ALIASES.get(raw, raw)


def runtime_bottom_to_top(names: Sequence[str]) -> list[int]:
    """镜像 Rigger.buildRig 的槽位重排，返回“运行时绘制顺序（先画在下）”的下标序列。"""
    count = len(names)
    order = list(range(count))
    head = [i for i in order if base_name(names[i]) in _HEAD_FEATURE_ORDER]
    head_sorted = sorted(head, key=lambda i: (_HEAD_FEATURE_ORDER[base_name(names[i])], i))
    for slot, i in zip(head, head_sorted):
        order[slot] = i
    return order


def _bake_mask_and_opacity(arr: np.ndarray, layer: PixelLayer) -> np.ndarray:
    """把有效蒙版和不透明度烘焙入像素；蒙版范围外采用 PSD 默认背景值。"""
    out = arr.copy()
    mask = layer.mask
    if mask is not None and not mask.disabled:
        mask_img = mask.topil()
        if mask_img is not None:
            plane = Image.new("L", (out.shape[1], out.shape[0]), mask.background_color)
            plane.paste(mask_img.convert("L"), (int(mask.left), int(mask.top)))
            out[..., 3] = (out[..., 3].astype(np.uint16) * np.asarray(plane, dtype=np.uint16) // 255).astype(np.uint8)
    opacity = layer.opacity
    if opacity != 255:
        out[..., 3] = (out[..., 3].astype(np.uint16) * opacity // 255).astype(np.uint8)
    return out


def _effective_layers(psd: PSDImage) -> tuple[list[np.ndarray], list[str]]:
    """按文件记录序（自底向上，同 ag-psd children 序，即客户端绘制序）返回
    每个扁平像素图层的画布级 RGBA。"""
    width, height = psd.width, psd.height
    arrays: list[np.ndarray] = []
    names: list[str] = []
    for layer in psd:
        if layer.is_group() or not hasattr(layer, "topil"):
            raise AppearanceError(
                "UNSUPPORTED_LAYER_SEMANTICS",
                f"layer {layer.name!r} is not a flat pixel layer; client rig requires flat PSD",
            )
        pixels = layer.topil()
        if pixels is None:
            raise AppearanceError(
                "UNSUPPORTED_LAYER_SEMANTICS",
                f"layer {layer.name!r} carries no pixel data",
            )
        if pixels.mode != "RGBA":
            pixels = pixels.convert("RGBA")
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(pixels, (int(layer.left), int(layer.top)))
        arr = _bake_mask_and_opacity(np.asarray(canvas, dtype=np.uint8), layer)
        arrays.append(arr)
        names.append(layer.name)
    return arrays, names


def _flood_from_border(blocked: np.ndarray) -> np.ndarray:
    """在非 blocked 区域内从画布边界洪泛，返回可达掩码。"""
    free = ~blocked
    reached = np.zeros_like(free)
    reached[0, :] = free[0, :]
    reached[-1, :] = free[-1, :]
    reached[:, 0] = free[:, 0]
    reached[:, -1] = free[:, -1]
    while True:
        grown = reached.copy()
        grown[1:, :] |= reached[:-1, :]
        grown[:-1, :] |= reached[1:, :]
        grown[:, 1:] |= reached[:, :-1]
        grown[:, :-1] |= reached[:, 1:]
        grown &= free
        if grown.sum() == reached.sum():
            return grown
        reached = grown


def source_foreground(source: SourceAppearance) -> np.ndarray:
    """透明图按 alpha；白底图只移除与边界连通的近白背景。

    封闭的浅色区域可能是皮肤高光、白衣或饰品，不能仅凭颜色当作背景。
    无 alpha 的图像无法可靠区分封闭白色背景与白色角色内容，保留原图像素。
    """
    arr = np.asarray(source.canonical, dtype=np.uint8)
    if source.alpha_mode == "transparent":
        fg = arr[..., 3] >= 8
    else:
        dist = 255 - np.min(arr[..., :3], axis=-1)
        fg = ~_flood_from_border(dist > 24)
    return fg


def alignment_foreground(source: SourceAppearance) -> np.ndarray:
    """对齐专用前景 = 可见前景 + 封闭背景孔洞。

    对齐用含孔洞的完整剪影做 IoU；透明源的孔洞只用于对齐，
    归属与评测仍以 source_foreground 为准。"""
    fg = source_foreground(source)
    outside = _flood_from_border(fg)
    return fg | ((~fg) & (~outside))


def _alignment_silhouette(
    names: Sequence[str],
    arrays: Sequence[np.ndarray],
    canvas_size: tuple[int, int],
) -> np.ndarray:
    """对齐专用画布剪影：角色语义层 claim 并集，再去掉近满幅填充与小连通域。

    see-through 可能把背景并入图层或输出角点 speckle；cleanAlpha 对“无 ≥40px
    分量”的层会整层保留，若直接用 claim 并集估包围盒，对齐初值会被整幅填充
    或噪声拉偏并误判 SOURCE_ALIGNMENT_FAILED。门禁合成仍按完整重建结果验收。"""
    width, height = canvas_size
    canvas_fg = np.zeros((height, width), dtype=bool)
    if not arrays:
        return canvas_fg
    canvas_area = max(1, width * height)
    for name, arr in zip(names, arrays):
        if base_name(name) not in _ALIGN_LAYER_BASE_NAMES:
            continue
        claim = _runtime_claim(arr[..., 3])
        if claim.sum() / canvas_area > _ALIGN_MAX_LAYER_COVERAGE:
            continue
        canvas_fg |= claim
    return _keep_large_components(canvas_fg, _RUNTIME_MIN_COMPONENT_PX)


def estimate_transform(
    source_fg: np.ndarray,
    canvas_fg: np.ndarray,
    canvas_size: tuple[int, int],
) -> AffineTransform:
    """多初值（包围盒 / letterbox / 填满画幅）+ 小范围 IoU 精调，返回最优等比变换。

    供应商画布会 letterbox 或重采样输入，角色剪影也可能略大于原图；只靠包围盒
    比 + ±1.5% 精调会在合法输出上误判对齐失败。"""
    if not source_fg.any() or not canvas_fg.any():
        raise AppearanceError("SOURCE_ALIGNMENT_FAILED", "empty foreground on source or split canvas")
    sx0, sy0, sx1, sy1 = _bbox(source_fg)
    cx0, cy0, cx1, cy1 = _bbox(canvas_fg)
    width, height = canvas_size
    src_h, src_w = source_fg.shape

    bbox_scale = ((cx1 - cx0) / (sx1 - sx0) + (cy1 - cy0) / (sy1 - sy0)) / 2.0
    bbox_ox = (cx0 + cx1) / 2.0 - bbox_scale * (sx0 + sx1) / 2.0
    bbox_oy = (cy0 + cy1) / 2.0 - bbox_scale * (sy0 + sy1) / 2.0
    letter_scale = min(width / src_w, height / src_h)
    letter_ox = (width - src_w * letter_scale) / 2.0
    letter_oy = (height - src_h * letter_scale) / 2.0
    fill_scale = max(width / src_w, height / src_h)
    fill_ox = (width - src_w * fill_scale) / 2.0
    fill_oy = (height - src_h * fill_scale) / 2.0
    seeds = [
        AffineTransform(bbox_scale, bbox_ox, bbox_oy),
        AffineTransform(letter_scale, letter_ox, letter_oy),
        AffineTransform(fill_scale, fill_ox, fill_oy),
        AffineTransform(letter_scale, bbox_ox, bbox_oy),
    ]

    factor = 4
    small_canvas = (
        np.asarray(
            Image.fromarray((canvas_fg * 255).astype(np.uint8)).resize(
                (max(1, width // factor), max(1, height // factor)),
                Image.BILINEAR,
            ),
        )
        > 127
    )
    src_img = Image.fromarray((source_fg * 255).astype(np.uint8))

    def iou_of(tr: AffineTransform) -> float:
        # 小图输出坐标 x 对应全图 x*factor：线性系数乘 factor，平移不变。
        a, b, c, d, e, f = tr.to_pil_coeffs()
        mapped = src_img.transform(
            (max(1, width // factor), max(1, height // factor)),
            Image.Transform.AFFINE,
            (a * factor, b * factor, c, d * factor, e * factor, f),
            resample=Image.BILINEAR,
        )
        small_src = np.asarray(mapped) > 127
        inter = int(np.logical_and(small_src, small_canvas).sum())
        union = int(np.logical_or(small_src, small_canvas).sum())
        return inter / union if union else 0.0

    best = seeds[0]
    best_iou = iou_of(best)
    for seed in seeds:
        base_iou = iou_of(seed)
        if base_iou > best_iou:
            best, best_iou = seed, base_iou
        for ds in _ALIGN_SCALE_DELTAS:
            for dx in _ALIGN_SHIFT_STEPS:
                for dy in _ALIGN_SHIFT_STEPS:
                    candidate = AffineTransform(seed.scale * (1.0 + ds), seed.offset_x + dx, seed.offset_y + dy)
                    score = iou_of(candidate)
                    if score > best_iou:
                        best, best_iou = candidate, score
    if best_iou < _ALIGN_IOU_MIN:
        raise AppearanceError(
            "SOURCE_ALIGNMENT_FAILED",
            f"source-to-canvas alignment IoU {best_iou:.3f} below {_ALIGN_IOU_MIN}",
        )
    return best


def _map_to_canvas(image: Image.Image, transform: AffineTransform, size: tuple[int, int]) -> Image.Image:
    return image.transform(size, Image.Transform.AFFINE, transform.to_pil_coeffs(), resample=Image.BICUBIC)


def _map_mask(mask: np.ndarray, transform: AffineTransform, size: tuple[int, int]) -> np.ndarray:
    mapped = Image.fromarray((mask * 255).astype(np.uint8)).transform(
        size,
        Image.Transform.AFFINE,
        transform.to_pil_coeffs(),
        resample=Image.BILINEAR,
    )
    return np.asarray(mapped) > 127


def _erode(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    img = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(size=radius * 2 + 1))
    return np.asarray(img) > 127


def _dilate(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    img = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(size=radius * 2 + 1))
    return np.asarray(img) > 127


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _keep_large_components(mask: np.ndarray, min_px: int) -> np.ndarray:
    """4 连通分量过滤：按行游程 + 并查集标注，返回 ≥min_px 分量的像素集。"""
    parent: list[int] = []
    runs_by_row: list[list[tuple[int, int, int]]] = []  # (x0, x1, run_id)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev: list[tuple[int, int, int]] = []
    for row_mask in mask:
        padded = np.concatenate(([False], row_mask, [False]))
        bounds = np.flatnonzero(padded[1:] != padded[:-1])
        row: list[tuple[int, int, int]] = []
        base = 0
        for k in range(0, bounds.size, 2):
            x0, x1 = int(bounds[k]), int(bounds[k + 1])
            rid = len(parent)
            parent.append(rid)
            row.append((x0, x1, rid))
            while base < len(prev) and prev[base][1] <= x0:
                base += 1
            j = base
            while j < len(prev) and prev[j][0] < x1:
                a, b = find(rid), find(prev[j][2])
                if a != b:
                    parent[max(a, b)] = min(a, b)
                j += 1
        runs_by_row.append(row)
        prev = row

    sizes: dict[int, int] = {}
    for row in runs_by_row:
        for x0, x1, rid in row:
            root = find(rid)
            sizes[root] = sizes.get(root, 0) + (x1 - x0)

    keep = np.zeros_like(mask)
    for y, row in enumerate(runs_by_row):
        for x0, x1, rid in row:
            if sizes[find(rid)] >= min_px:
                keep[y, x0:x1] = True
    return keep


def _runtime_visible(alpha: np.ndarray) -> np.ndarray | None:
    """镜像 rigger.js cleanAlpha 的运行时可见区域；无任何 ≥阈值分量时整层
    原样保留（cleanAlpha 的早返回），返回 None 表示不清洗。"""
    large = _keep_large_components(alpha > _RUNTIME_LABEL_ALPHA, _RUNTIME_MIN_COMPONENT_PX)
    if not large.any():
        return None
    return _dilate(large, _RUNTIME_CLEAN_DILATE_PX)


def _runtime_claim_and_visible(alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """运行时 claim 区域与 cleanAlpha 可见掩码（无大分量时不清洗，visible 为 None）。"""
    visible = _runtime_visible(alpha)
    present = alpha >= _CLAIM_ALPHA
    claim = present if visible is None else (present & visible)
    return claim, visible


def _runtime_claim(alpha: np.ndarray) -> np.ndarray:
    """该图层在运行时会遮挡/可见的存在区域（claim 口径，已镜像 cleanAlpha）。"""
    return _runtime_claim_and_visible(alpha)[0]


def rebuild_source_locked(source: SourceAppearance, provider_psd: bytes) -> RebuiltAppearance:
    """回填原图像素并写出规范化 PSD；结构不支持或对齐失败抛 AppearanceError。"""
    try:
        psd = PSDImage.open(io.BytesIO(provider_psd))
    except Exception as exc:
        raise AppearanceError("PSD_COMPOSITE_MISMATCH", f"provider PSD is not parseable: {exc}") from exc

    arrays, names = _effective_layers(psd)
    runtime = runtime_bottom_to_top(names)
    width, height = psd.width, psd.height

    source_fg = alignment_foreground(source)
    canvas_fg = _alignment_silhouette(names, arrays, (width, height))
    transform = estimate_transform(source_fg, canvas_fg, (width, height))

    mapped_arr = np.asarray(_map_to_canvas(source.canonical, transform, (width, height)), dtype=np.uint8)
    fg_mapped = _map_mask(source_foreground(source), transform, (width, height))

    translucent = fg_mapped & (mapped_arr[..., 3] < 255)
    transparent = (mapped_arr[..., 3] == 0) if source.alpha_mode == "transparent" else None
    claimed = np.zeros((height, width), dtype=bool)
    rebuilt: dict[int, np.ndarray] = {}
    stats_by_index: dict[int, LayerStat] = {}
    # 自运行时最上层向下分配可见像素归属：可见区域归该层并整像素（RGB+Alpha）
    # 回填自原图——半透明纱、羽化边缘一并保真。可见与归属口径都按客户端
    # cleanAlpha 后的运行时效果判定，客户端会清零的像素同样从产物中移除，
    # 门禁合成与运行时渲染保持一致；透明源的孔洞不归属。
    for index in reversed(runtime):
        eff = arrays[index]
        claim, visible = _runtime_claim_and_visible(eff[..., 3])
        own = claim & fg_mapped & ~claimed
        claimed |= claim
        out = eff.copy()
        out[own] = mapped_arr[own]
        # 半透明原图已经是合成后的外观，不能再叠加底层生成色或 Alpha。
        out[translucent & ~own, 3] = 0
        if transparent is not None:
            out[transparent, 3] = 0
        if visible is not None:
            out[~visible, 3] = 0
        rebuilt[index] = out
        stats_by_index[index] = LayerStat(
            name=names[index],
            owned_pixels=int(own.sum()),
            kept_pixels=int((claim & ~own & (out[..., 3] > 0)).sum()),
        )

    # 根容器必须 RGBA：RGB 模式 PSD 写入时会把图层 Alpha 展平成不透明，
    # 客户端与验收合成都会拿到矩形级实色块。
    out_psd = PSDImage.new(mode="RGBA", size=(width, height), color=(255, 255, 255, 0))
    # PSD 文件记录、psd-tools 迭代与 ag-psd children 同为自底向上，append
    # 后 append 在上：按运行时自底向上顺序 append 后，客户端按 children 序
    # 绘制即还原运行时层序。
    for index in runtime:
        layer_arr = rebuilt[index]
        ys, xs = np.nonzero(layer_arr[..., 3])
        if ys.size == 0:
            continue
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()) + 1, int(ys.max()) + 1
        crop = Image.fromarray(layer_arr[y0:y1, x0:x1])
        out_psd.append(out_psd.create_pixel_layer(crop, name=names[index], top=y0, left=x0))
    buffer = io.BytesIO()
    out_psd.save(buffer)
    return RebuiltAppearance(
        psd_bytes=buffer.getvalue(),
        canvas=(width, height),
        transform=transform,
        layer_stats=[stats_by_index[i] for i in runtime if i in stats_by_index],
        layer_names_bottom_to_top=[layer.name for layer in out_psd],
    )
