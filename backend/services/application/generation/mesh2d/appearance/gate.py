"""外观质量门禁：验收对象是重建 PSD 实际图层的重合成结果，不是内嵌预览。

门禁在统一画布坐标系下与标准化原图比较：
- 全局内部前景（远离轮廓，含半透明像素）逐像素 ΔE00；
- 脸部区域（必须存在归一名为 face 的脸底 + 五官图层声明范围的保守扩张）
  同口径 ΔE00，且脸区 Alpha 与原图一致；
- 覆盖率：原图可见前景中没有任何图层声明的像素占比。
透明源孔洞与轮廓羽化带不参与比较。ΔE00 阈值为平均 ≤1、P95 ≤3；
脸区连续明显色差另行拦截。评测口径为同画布、BICUBIC 对齐重采样、透明背景合成。
"""

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from psd_tools import PSDImage

from .rebuild import (
    _CLAIM_ALPHA,
    _EDGE_BAND,
    AppearanceError,
    RebuiltAppearance,
    _bbox,
    _dilate,
    _erode,
    _keep_large_components,
    _map_mask,
    base_name,
    normalize_name,
    source_foreground,
)
from .source_asset import SourceAppearance

DELTA_E_MEAN_MAX = 1.0
DELTA_E_P95_MAX = 3.0
ALPHA_TOLERANCE = 2
MAX_UNCOVERED_RATIO = 0.001
# 平均值和分位数会漏掉占脸区不足 5% 的斑块；独立拦截连续 9px 的明显色差。
FACE_PATCH_MIN_PIXELS = 9

# 参与脸部区域构成的图层（脸底 + 五官；不含 front hair 发际遮挡）。
# 名称经 base_name 归一：see-through 原始层名 mouth 会归一为 mouth_open。
_FACE_ZONE_NAMES = {
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
}
_FACE_ZONE_DILATE = 4


@dataclass
class GateReport:
    passed: bool
    code: str | None = None
    metrics: dict[str, float | int | bool | list[int]] = field(default_factory=dict)
    composite: Image.Image | None = None  # 实际图层重合成（画布坐标 RGBA）

    def to_json(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "code": self.code,
            "metrics": self.metrics,
        }


def _srgb_to_lab(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """sRGB(0-255, float) → CIEL* a* b*（D65）。"""
    c = rgb / 255.0
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

    # 行 = XYZ 分量，列 = R/G/B；linear @ M.T 得到 (N,3) 的 XYZ。
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float64,
    )
    xyz = linear @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    t = xyz / white
    delta = 6.0 / 29.0
    f = np.where(t > delta**3, np.cbrt(t), t / (3 * delta**2) + 4.0 / 29.0)
    l_star = 116.0 * f[..., 1] - 16.0
    a_star = 500.0 * (f[..., 0] - f[..., 1])
    b_star = 200.0 * (f[..., 1] - f[..., 2])
    return l_star, a_star, b_star


def _delta_e00(
    lab1: tuple[np.ndarray, np.ndarray, np.ndarray],
    lab2: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> np.ndarray:
    """CIEDE2000（Sharma 2005 实现式，kL=kC=kH=1），逐像素。

    实现已对齐 scikit-image deltaE_ciede2000（Sharma 全量测试向量）。"""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    g = 0.5 * (1.0 - np.sqrt(c_bar**7 / (c_bar**7 + 25.0**7)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = np.hypot(a1p, b1)
    c2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0

    dl = l2 - l1
    dc = c2p - c1p
    dhp = h2p - h1p
    dhp = np.where(c1p * c2p == 0.0, 0.0, dhp)
    dhp = np.where(dhp > 180.0, dhp - 360.0, dhp)
    dhp = np.where(dhp < -180.0, dhp + 360.0, dhp)
    dh = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp) / 2.0)

    l_bar = (l1 + l2) / 2.0
    cp_bar = (c1p + c2p) / 2.0
    sum_h = h1p + h2p
    h_bar = np.where(
        np.abs(h1p - h2p) <= 180.0,
        sum_h / 2.0,
        np.where(sum_h < 360.0, (sum_h + 360.0) / 2.0, (sum_h - 360.0) / 2.0),
    )
    h_bar = np.where(c1p * c2p == 0.0, sum_h, h_bar)

    t = (
        1.0
        - 0.17 * np.cos(np.radians(h_bar - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * h_bar))
        + 0.32 * np.cos(np.radians(3.0 * h_bar + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * h_bar - 63.0))
    )
    d_theta = 30.0 * np.exp(-(((h_bar - 275.0) / 25.0) ** 2))
    r_t = -2.0 * np.sqrt(cp_bar**7 / (cp_bar**7 + 25.0**7)) * np.sin(np.radians(2.0 * d_theta))

    s_l = 1.0 + 0.015 * (l_bar - 50.0) ** 2 / np.sqrt(20.0 + (l_bar - 50.0) ** 2)
    s_c = 1.0 + 0.045 * cp_bar
    s_h = 1.0 + 0.015 * cp_bar * t
    term_l = dl / s_l
    term_c = dc / s_c
    term_h = dh / s_h
    return np.sqrt(term_l**2 + term_c**2 + term_h**2 + r_t * term_c * term_h)


def _psd_composite(psd_bytes: bytes) -> tuple[Image.Image, PSDImage]:
    """从最终产物出发重合成实际图层（ignore_preview，禁止用缓存预览顶替）。"""
    try:
        psd = PSDImage.open(io.BytesIO(psd_bytes))
        composite = psd.composite(ignore_preview=True)
    except Exception as exc:
        raise AppearanceError("PSD_COMPOSITE_MISMATCH", f"rebuilt PSD is not parseable: {exc}") from exc
    if composite is None:
        raise AppearanceError("PSD_COMPOSITE_MISMATCH", "rebuilt PSD has no compositeable layers")
    if composite.mode != "RGBA":
        composite = composite.convert("RGBA")
    return composite, psd


def run_gate(rebuilt: RebuiltAppearance, source: SourceAppearance) -> GateReport:
    """门禁 A 的可执行判定：重建 PSD 实际图层重合成 ≑ 标准化原图。"""
    composite, psd = _psd_composite(rebuilt.psd_bytes)
    width, height = rebuilt.canvas
    transform = rebuilt.transform

    fg_mapped = _map_mask(source_foreground(source), transform, (width, height))
    interior = _erode(fg_mapped, _EDGE_BAND)

    comp = np.asarray(composite, dtype=np.uint8)
    alpha = comp[..., 3]
    comp_alpha_ok = alpha >= _CLAIM_ALPHA
    eval_mask = interior & comp_alpha_ok

    mapped_ref = source.canonical.transform(
        (width, height),
        Image.Transform.AFFINE,
        transform.to_pil_coeffs(),
        resample=Image.BICUBIC,
    )
    ref = np.asarray(mapped_ref, dtype=np.uint8)

    metrics: dict[str, float | int | bool | list[int]] = {"canvas": [width, height], "layers": len(list(psd))}
    alpha_mismatch = np.abs(alpha.astype(np.int16) - ref[..., 3].astype(np.int16)) > ALPHA_TOLERANCE
    alpha_mismatch_ratio = float((interior & alpha_mismatch).sum()) / max(1, int(interior.sum()))
    metrics["alpha_mismatch_interior_ratio"] = round(alpha_mismatch_ratio, 6)
    transparent_violations = 0
    if source.alpha_mode == "transparent":
        transparent_violations = int(((ref[..., 3] == 0) & (alpha > ALPHA_TOLERANCE)).sum())
    metrics["transparent_source_violations"] = transparent_violations

    # 未覆盖判定只在内部前景上做：轮廓边缘带与透明源孔洞不计入。
    uncovered_ratio = float(np.logical_and(interior, alpha < _CLAIM_ALPHA).sum()) / max(1, int(interior.sum()))
    metrics["uncovered_interior_ratio"] = round(uncovered_ratio, 6)

    global_de = np.array([], dtype=np.float64)
    if eval_mask.sum() >= 1:
        global_de = _delta_e00(
            _srgb_to_lab(comp[eval_mask, :3].astype(np.float64)),
            _srgb_to_lab(ref[eval_mask, :3].astype(np.float64)),
        )
        metrics["global_delta_e_mean"] = round(float(global_de.mean()), 4)
        metrics["global_delta_e_p95"] = round(float(np.percentile(global_de, 95)), 4)
        metrics["global_pixels"] = int(eval_mask.sum())

    # 脸部区域：必须存在归一名为 face 的脸底；再叠加五官图层声明范围的保守扩张。
    # 仅有 mouth 等五官、缺少 face 时客户端会退回画布中心锚点，门禁不得放行。
    face_zone = np.zeros((height, width), dtype=bool)
    has_face_base = False
    for layer in psd:
        layer_base = base_name(layer.name)
        if normalize_name(layer.name) == "face":
            has_face_base = True
        if layer_base not in _FACE_ZONE_NAMES:
            continue
        pixels = layer.topil()
        if pixels is None:
            continue
        if pixels.mode != "RGBA":
            pixels = pixels.convert("RGBA")
        plane = np.asarray(pixels)[..., 3]
        top, left = int(layer.top), int(layer.left)
        y0, x0 = max(0, top), max(0, left)
        y1 = min(height, top + plane.shape[0])
        x1 = min(width, left + plane.shape[1])
        if y1 <= y0 or x1 <= x0:
            continue
        # 图层包围盒可能伸出画布：与 rebuild 写出路径一致，裁剪后再并入。
        face_zone[y0:y1, x0:x1] |= plane[y0 - top : y1 - top, x0 - left : x1 - left] >= _CLAIM_ALPHA
    face_zone = _dilate(face_zone, _FACE_ZONE_DILATE) & interior

    face_de = np.array([], dtype=np.float64)
    face_alpha_violations = 0
    if not has_face_base or face_zone.sum() < 1:
        # 没有脸底或脸区为空时不得以“全局通过”放行——脸部检查独立成立。
        return GateReport(
            passed=False,
            code="FACE_APPEARANCE_MISMATCH",
            metrics={**metrics, "face_pixels": int(face_zone.sum()), "has_face_base": has_face_base},
            composite=composite,
        )
    face_eval = face_zone & comp_alpha_ok
    if face_eval.sum() >= 1:
        face_de = _delta_e00(
            _srgb_to_lab(comp[face_eval, :3].astype(np.float64)),
            _srgb_to_lab(ref[face_eval, :3].astype(np.float64)),
        )
    # 半透明源同样比较 Alpha，不能以“不透明像素不足”为由跳过验收。
    face_alpha_violations = int((face_zone & alpha_mismatch).sum())
    fx0, fy0, fx1, fy1 = _bbox(face_zone)
    metrics["face_pixels"] = int(face_zone.sum())
    metrics["face_bbox"] = [fx0, fy0, fx1, fy1]
    metrics["face_alpha_violations"] = face_alpha_violations
    if face_de.size:
        metrics["face_delta_e_mean"] = round(float(face_de.mean()), 4)
        metrics["face_delta_e_p95"] = round(float(np.percentile(face_de, 95)), 4)
    face_bad = np.zeros((height, width), dtype=bool)
    face_bad[face_eval] = face_de > DELTA_E_P95_MAX
    face_patch_pixels = int(_keep_large_components(face_bad, FACE_PATCH_MIN_PIXELS).sum())
    metrics["face_mismatch_patch_pixels"] = face_patch_pixels

    passed = alpha_mismatch_ratio <= MAX_UNCOVERED_RATIO and transparent_violations == 0
    code = None if passed else "PSD_COMPOSITE_MISMATCH"
    if global_de.size and (
        metrics["global_delta_e_mean"] > DELTA_E_MEAN_MAX or metrics["global_delta_e_p95"] > DELTA_E_P95_MAX
    ):
        passed, code = False, "PSD_COMPOSITE_MISMATCH"
    if face_de.size and (
        metrics["face_delta_e_mean"] > DELTA_E_MEAN_MAX or metrics["face_delta_e_p95"] > DELTA_E_P95_MAX
    ):
        passed, code = False, "FACE_APPEARANCE_MISMATCH"
    if face_alpha_violations > 0:
        passed, code = False, "FACE_APPEARANCE_MISMATCH"
    if face_patch_pixels > 0:
        passed, code = False, "FACE_APPEARANCE_MISMATCH"
    if uncovered_ratio > MAX_UNCOVERED_RATIO:
        passed, code = False, "SOURCE_ALIGNMENT_FAILED"

    return GateReport(passed=passed, code=code, metrics=metrics, composite=composite)
