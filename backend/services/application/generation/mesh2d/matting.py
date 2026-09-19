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


def has_transparent_background(raw: bytes) -> bool:
    """判断图像是否自带可用透明背景（自备姿态图跳过抠图的依据）。

    同时要求存在可观比例的透明像素且画布边框环基本透明：仅局部透明（圆角、水印
    擦除）或边框仍不透明（背景未去净）的图不视为已抠图，仍走抠图管线。
    """
    with Image.open(io.BytesIO(raw)) as source:
        alpha = np.asarray(source.convert("RGBA").getchannel("A"), dtype=np.uint8)
    if float((alpha < 16).mean()) < 0.05:
        return False
    # 边框环过半透明即可：容忍主体贴边（脚底、头顶裁切）的合法构图；背景未去净的图
    # 边框几乎全不透明，仍会落到抠图管线。
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    return bool((border < 16).mean() >= 0.5)


def subject_matte(raw: bytes) -> Image.Image | None:
    """对姿态图跑 ISNet，返回以蒙版为 alpha 的 RGBA；模型缺失或推理失败返回 None。

    ISNet 输出显著性概率而非硬分割。用较宽软阈值映射：低置信背景压成透明，高置信
    主体保持不透明，边缘保留过渡带。原先接近阶跃的硬二值会把近景残留整片变成
    不透明色块，贴边合成到深色房间时尤其明显。
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
        # 软阈值约 0.35→透明、0.65→不透明；比 (p-0.5)*10 更能保留发丝/光晕边缘，
        # 同时把大片低置信背景压掉，避免半透明残留在合成时露出底色。
        soft = np.clip((matte - 0.35) / 0.30, 0.0, 1.0)
        alpha = Image.fromarray((soft * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
        rgba = original.convert("RGBA")
        rgba.putalpha(alpha.resize(original.size, Image.Resampling.BILINEAR))
        return rgba
    except Exception:
        logger.warning("matting inference failed; falling back to chroma cutout")
        return None
