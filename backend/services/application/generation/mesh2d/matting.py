"""ISNet 显著性抠图：把姿态生成图的角色从任意背景中分离为透明纹理。

模型文件按 `data_dir/models/<name>.onnx` 查找（config.toml `matting_model`），随 data 卷挂载；
缺失或推理失败由调用方退回色键路径，本模块不负责下载。
推理会话进程级缓存，ISNet 输入固定 1024x1024，CPU 单次推理约数百毫秒，仅姿态包生成时调用。
"""

import io
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from components import SETTINGS, get_logger
from PIL import Image, ImageFilter

logger = get_logger(__name__)
INPUT_SIZE = 1024

_session: ort.InferenceSession | None = None
_lock = threading.Lock()


def _model_path() -> Path:
    return Path(SETTINGS.data_dir) / "models" / f"{SETTINGS.matting_model}.onnx"


def _get_session() -> ort.InferenceSession:
    """会话按首次调用时的模型路径建立并进程级缓存；matting_model 运行期不热切换。"""
    global _session
    with _lock:
        if _session is not None:
            return _session
        path = _model_path()
        if not path.is_file():
            raise FileNotFoundError(f"抠图模型缺失：{path}")
        _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return _session


def _normalize(image: Image.Image) -> np.ndarray:
    # ISNet 官方预处理：0..1 缩放后减 0.5 均值居中（std 恒为 1，无缩放）。
    return np.asarray(image, dtype=np.float32) / 255.0 - 0.5


def subject_matte(raw: bytes) -> Image.Image | None:
    """对姿态图跑 ISNet，返回以蒙版为 alpha 的 RGBA；模型缺失或推理失败返回 None。

    ISNet 输出显著性概率而非硬分割，蒙版经二值 + 轻微羽化，避免半透明噪点整片残留。
    """
    try:
        session = _get_session()
    except FileNotFoundError:
        logger.warning(
            "matting model unavailable; falling back to chroma cutout",
            extra={"model": SETTINGS.matting_model},
        )
        return None
    except Exception:
        logger.warning("matting session init failed; falling back to chroma cutout")
        return None
    try:
        with Image.open(io.BytesIO(raw)) as source:
            original = source.convert("RGB")
            resized = original.resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.LANCZOS)
            feed = np.transpose(_normalize(resized), (2, 0, 1))[None]
            results = session.run(None, {"input_image": feed.astype(np.float32)})
        # 首个输出为主显著性图（后续为内部特征图），值域 0..1 已是概率，无需再过 sigmoid。
        matte = results[0][0, 0]
        hard = np.clip((matte - 0.5) * 10 * 255, 0, 255).astype(np.uint8)
        alpha = Image.fromarray(hard).filter(ImageFilter.GaussianBlur(1.5))
        rgba = original.convert("RGBA")
        rgba.putalpha(alpha.resize(original.size, Image.Resampling.BILINEAR))
        return rgba
    except Exception:
        logger.warning("matting inference failed; falling back to chroma cutout")
        return None
