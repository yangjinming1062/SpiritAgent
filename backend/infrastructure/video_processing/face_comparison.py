"""测量同一时间、同一表演的成片局部是否持续丢失原先可辨的结构。"""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


def _luminance(data: bytes) -> np.ndarray:
    with Image.open(BytesIO(data)) as image:
        return np.asarray(image.convert("L").resize((160, 160), Image.Resampling.LANCZOS), dtype=np.float32)


def _lost_structure(original: bytes, delivered: bytes) -> bool:
    before, after = _luminance(original), _luminance(delivered)
    # 两次面部定位的裁边可能不同。只有能按全脸纹理可靠配准的同刻局部才可比较。
    try:
        correlation, transform = cv2.findTransformECC(
            cv2.GaussianBlur(before, (0, 0), 1.2),
            cv2.GaussianBlur(after, (0, 0), 1.2),
            np.eye(2, 3, dtype=np.float32),
            cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 65, 1e-5),
        )
    except cv2.error:
        return False
    if (
        not np.isfinite(correlation)
        or not np.isfinite(transform).all()
        or correlation < 0.8
        or np.max(np.abs(transform[:, :2] - np.eye(2))) > 0.18
        or np.max(np.abs(transform[:, 2])) > 20
    ):
        return False
    aligned = cv2.warpAffine(
        after, transform, (160, 160), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE
    )
    # 检查定位框内部的重叠小块。亮度、原始细纹能量和边缘方向同时明显退化才计入。
    rows, columns = range(34, 103, 10), range(29, 102, 10)
    losses = np.zeros((len(rows), len(columns)), dtype=np.uint8)
    for row, y in enumerate(rows):
        for col, x in enumerate(columns):
            source = before[y : y + 23, x : x + 23]
            candidate = aligned[y : y + 23, x : x + 23]
            if source.std() < 12:
                continue
            edges = source - cv2.GaussianBlur(source, (0, 0), 1.4)
            strength = np.linalg.norm(edges)
            if strength < 55:
                continue
            fine = candidate - cv2.GaussianBlur(candidate, (0, 0), 1.4)
            retained = np.linalg.norm(fine)
            if retained >= 0.70 * strength:
                continue
            source_centered, target_centered = source - source.mean(), candidate - candidate.mean()
            tone = np.sum(source_centered * target_centered) / max(
                1e-6, np.linalg.norm(source_centered) * np.linalg.norm(target_centered)
            )
            alignment = np.sum(edges * fine) / max(1e-6, strength * retained)
            losses[row, col] = tone < 0.65 and alignment < 0.45
    _, _, components, _ = cv2.connectedComponentsWithStats(losses, connectivity=8)
    return bool(np.any(components[1:, cv2.CC_STAT_AREA] >= 3))


def face_structure_loss_times(pairs: list[tuple[float, bytes, bytes]]) -> tuple[float, ...]:
    times = tuple(t for t, original, delivered in pairs if _lost_structure(original, delivered))
    # 一个采样的软边或短暂遮挡交给视觉检查。只有多个分开的实际时刻才约束成片。
    return times if len(times) >= 2 and times[-1] - times[0] >= 0.15 else ()
