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


@lru_cache(maxsize=1)
def _session(path: Path, _modified_ns: int) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


def _crop_face(image: Image.Image, session: ort.InferenceSession) -> Image.Image | None:
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
    return image.crop(
        (
            max(0, round((left - pad_x) * image.width)),
            max(0, round((top - pad_y) * image.height)),
            min(image.width, round((right + pad_x) * image.width)),
            min(image.height, round((bottom + pad_y) * image.height)),
        )
    )


def review_face_details(frames: tuple[bytes, ...], frame_times: tuple[float, ...]) -> tuple[tuple[float, bytes], ...]:
    path = Path(SETTINGS.data_dir) / "models" / _MODEL
    if not path.is_file():
        return ()
    details: list[tuple[float, bytes]] = []
    try:
        session = _session(path, path.stat().st_mtime_ns)
        count = min(5, len(frames))
        for slot in range(count):
            index = round(slot * (len(frames) - 1) / max(1, count - 1))
            with Image.open(BytesIO(frames[index])) as image:
                face = _crop_face(image, session)
            if face is not None:
                with face, BytesIO() as output:
                    face.save(output, format="WEBP", lossless=True)
                    details.append((frame_times[index], output.getvalue()))
    except Exception:
        logger.warning("Video face detail unavailable", exc_info=True)
    return tuple(details)
