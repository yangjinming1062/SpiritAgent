"""语义前景透明化：保留角色内部颜色；静止区域做有限时域稳定，边缘去背景污染。"""

import tempfile
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from components import SETTINGS
from numpy.typing import NDArray
from PIL import Image

from .ffmpeg import VideoProcessError, alpha_input_args, ffmpeg_stdout, probe_video, run_ffmpeg

Array = NDArray[np.float32]


def require_matting_model() -> Path:
    path = Path(SETTINGS.data_dir) / "models" / f"{SETTINGS.matting_model}.onnx"
    if not path.is_file():
        raise VideoProcessError("抠像模型未就绪，请配置后再生成动作", internal=f"missing {path}")
    return path


class ForegroundMatte:
    """ISNet 只确定前景，不以颜色距离删除皮肤、衣物或毛发。"""

    def __init__(self, model_path: Path) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.previous_rgb: Array | None = None
        self.previous_alpha: Array | None = None

    def apply(self, image: Image.Image) -> Image.Image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        resized = image.convert("RGB").resize((1024, 1024), Image.Resampling.BILINEAR)
        tensor = (np.asarray(resized, dtype=np.float32) / 255.0 - 0.5).transpose(2, 0, 1)[None]
        mask = np.squeeze(self.session.run(None, {self.input_name: tensor})[0]).astype(np.float32)
        if mask.ndim != 2 or not np.isfinite(mask).all():
            raise VideoProcessError("抠像模型返回无效遮罩")
        # 不逐帧 min/max 拉伸，否则每帧概率标尺变化会放大边缘闪烁。
        alpha = np.asarray(Image.fromarray(np.clip(mask, 0, 1)).resize(image.size, Image.Resampling.BILINEAR))
        alpha = np.clip((alpha - 0.02) / 0.96, 0, 1)
        if self.previous_rgb is not None and self.previous_alpha is not None and rgb.shape == self.previous_rgb.shape:
            stationary = np.max(np.abs(rgb - self.previous_rgb), axis=2) < 3 / 255
            consistent = np.abs(alpha - self.previous_alpha) < 0.15
            alpha = np.where(stationary & consistent, 0.75 * alpha + 0.25 * self.previous_alpha, alpha)
        self.previous_rgb, self.previous_alpha = rgb, alpha
        # 只在均匀背景且语义边缘半透明时去背景混色；实体 RGB 不改动。
        border = np.concatenate(
            (rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3), rgb[:, -3:].reshape(-1, 3)),
        )
        background = np.median(border, axis=0)
        if float(np.quantile(np.abs(border - background), 0.9)) < 0.035:
            edge = (alpha > 0.08) & (alpha < 0.98)
            foreground = (rgb - (1 - alpha[..., None]) * background) / np.maximum(alpha[..., None], 0.08)
            rgb = np.where(edge[..., None], np.clip(foreground, 0, 1), rgb)
        rgba = np.concatenate((rgb, alpha[..., None]), axis=2)
        return Image.fromarray(np.round(rgba * 255).astype(np.uint8))


def matte_video(src: Path, dst: Path) -> None:
    """保留有效原生 alpha，否则逐帧 ISNet 抠像；输出 FFV1/BGRA 中间片段。"""
    probe = probe_video(src)
    if probe.width * probe.height > 3840 * 2160:
        raise VideoProcessError("动作素材分辨率超出处理上限")
    dst.parent.mkdir(parents=True, exist_ok=True)
    native_alpha = False
    if probe.has_alpha:
        # 在原分辨率上取最小 alpha；缩小后再判断会抹掉细窄的透明边缘。
        stats = ffmpeg_stdout(
            [
                *alpha_input_args(probe),
                "-i",
                str(src),
                "-vf",
                "alphaextract,scale=in_range=full:out_range=full,format=yuv444p,"
                "signalstats,metadata=mode=print:key=lavfi.signalstats.YMIN:file=-",
                "-f",
                "null",
                "-",
            ],
            failure_message="视频透明通道解码失败",
        )
        minima = [
            int(line.split("=", 1)[1])
            for line in stats.decode().splitlines()
            if line.startswith("lavfi.signalstats.YMIN=")
        ]
        if not minima:
            raise VideoProcessError("视频透明通道解码失败")
        native_alpha = min(minima) <= 8
    if native_alpha:
        run_ffmpeg(
            [*alpha_input_args(probe), "-i", str(src), "-an", "-c:v", "ffv1", "-pix_fmt", "bgra", str(dst)],
            label="透明解码",
        )
        return
    matte = ForegroundMatte(require_matting_model())
    deadline = time.monotonic() + 600
    with tempfile.TemporaryDirectory(prefix="video-matte-") as tmp:
        work = Path(tmp)
        frames = work / "frames"
        frames.mkdir()
        run_ffmpeg(["-i", str(src), "-vf", "fps=24", str(frames / "f%05d.png")], label="抽帧")
        paths = sorted(frames.glob("f*.png"))
        if not paths:
            raise VideoProcessError("视频未解码出有效画面")
        for path in paths:
            if time.monotonic() > deadline:
                raise VideoProcessError("视频抠像超时")
            with Image.open(path) as frame:
                result = matte.apply(frame)
            result.save(path)
        run_ffmpeg(
            ["-framerate", "24", "-i", str(frames / "f%05d.png"), "-an", "-c:v", "ffv1", "-pix_fmt", "bgra", str(dst)],
            label="透明化",
        )
