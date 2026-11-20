"""从视频原始抽样中定位单一清晰面部，供身份复核查看局部细节。"""

from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger

logger = get_logger(__name__)
_MODEL = "face-detector-rfb-640.onnx"


def review_model_path(name: str) -> Path:
    configured = Path(SETTINGS.data_dir) / "models" / name
    return configured if configured.is_file() else Path("/opt/spiritagent/models") / name


@lru_cache(maxsize=1)
def _session(path: Path, _modified_ns: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def _face_box(image: Image.Image, session: ort.InferenceSession) -> tuple[int, int, int, int] | None:
    resized = image.convert("RGB").resize((640, 480), Image.Resampling.BILINEAR)
    rgb = (np.asarray(resized, dtype=np.float32) - 127) / 128
    scores, boxes = session.run(["scores", "boxes"], {"input": rgb.transpose(2, 0, 1)[None]})
    confident = scores[0, :, 1] >= 0.9
    boxes = np.clip(boxes[0, confident], 0, 1)
    if not len(boxes):
        return None
    candidates = scores[0, confident, 1]
    best = boxes[int(candidates.argmax())]
    left, top, right, bottom = best
    width, height = right - left, bottom - top
    if width * image.width < 20 or height * image.height < 24:
        return None
    # 同一张脸的候选框重叠。画面出现另一张高置信度面部时保留全帧，由复核模型判断主体。
    centers = (boxes[:, :2] + boxes[:, 2:]) / 2
    if ((np.abs(centers - (best[:2] + best[2:]) / 2) > (width, height)).any(axis=1)).any():
        return None
    pad_x, pad_y = width * 0.45, height * 0.35
    return (
        max(0, round((left - pad_x) * image.width)),
        max(0, round((top - pad_y) * image.height)),
        min(image.width, round((right + pad_x) * image.width)),
        min(image.height, round((bottom + pad_y) * image.height)),
    )


def subject_horizontal_span(frames: tuple[bytes, ...]) -> tuple[float, float]:
    """为动作局部选择全段共用的横向取景，主体不明时保留全宽。"""
    path = review_model_path(_MODEL)
    if not path.is_file() or not frames:
        return 0.0, 1.0
    try:
        session = _session(str(path), path.stat().st_mtime_ns)
        centers = []
        for index in sorted({0, len(frames) // 2, len(frames) - 1}):
            with Image.open(BytesIO(frames[index])) as image:
                box = _face_box(image, session)
                if box is None:
                    return 0.0, 1.0
                centers.append((box[0] + box[2]) / (2 * image.width))
        left, right = min(centers), max(centers)
        width = min(1.0, right - left + 0.52)
        start = min(1.0 - width, max(0.0, (left + right - width) / 2))
        return start, start + width
    except Exception:
        logger.warning("Video subject layout unavailable", exc_info=True)
        return 0.0, 1.0


def review_face_details(frames: tuple[bytes, ...], frame_times: tuple[float, ...]) -> tuple[tuple[float, bytes], ...]:
    path = review_model_path(_MODEL)
    if not path.is_file():
        return ()
    details: list[tuple[float, bytes]] = []
    try:
        session = _session(path, path.stat().st_mtime_ns)
        count = min(5, len(frames))
        for slot in range(count):
            index = round(slot * (len(frames) - 1) / max(1, count - 1))
            with Image.open(BytesIO(frames[index])) as image:
                box = _face_box(image, session)
                face = image.crop(box) if box else None
            if face is not None:
                with face, BytesIO() as output:
                    face.save(output, format="WEBP", lossless=True)
                    details.append((frame_times[index], output.getvalue()))
    except Exception:
        logger.warning("Video face detail unavailable", exc_info=True)
    return tuple(details)
