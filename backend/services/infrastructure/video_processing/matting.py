"""角色视频背景透明化：背景近纯色时整段色键（FFmpeg colorkey），否则 ISNet onnx 逐帧抠像。

键色由低分辨率预扫从画面边缘采样；角色与键色同色导致色键穿透时按覆盖率护栏回退 ISNet。
ISNet 模型文件位于 ``data_dir/models/<matting_model>.onnx``（SETTINGS.matting_model 槽位），
缺失且背景不纯色时明确失败。

产物为 FFV1/MKV 中间视频（像素级 yuva420p alpha，可被 FFmpeg 滤镜无损解码）：后续分割、
遮罩与封面都依赖像素级 alpha，而交付用 WebM/VP9 的 alpha 走 BlockAdditional 旁路，
FFmpeg CLI 解码不可见（见 ffmpeg.probe_alpha_side_data），不能用于内部处理。
"""

import statistics
import tempfile
from dataclasses import dataclass
from pathlib import Path

from components import SETTINGS, get_logger

from .ffmpeg import VideoProcessError, _binary, _run, probe_video, run_ffmpeg
from .segmentation import alpha_coverage_series

logger = get_logger(__name__)

# 中间产物编码：FFV1 无损 + 像素级 alpha
_FFV1_ALPHA_ARGS: tuple[str, ...] = ("-c:v", "ffv1", "-pix_fmt", "yuva420p")

# 预扫：边缘带采样判定背景纯度。宽度取 256——过窄的面积缩放会把纹理/噪声背景低通抹平成
# 纯色（64px 时 8×12 像素平均后噪声 MAD 跌破阈值），256px 下噪声 MAD 仍显著高于纯色。
# 逐帧边框 MAD（对角色贴边鲁棒）+ 跨帧中位色漂移；色键 similarity（≈0.22×441≈97 欧氏色距）
# 覆盖纯色内残余噪声，角色同色穿透另有覆盖率护栏。
_SCAN_WIDTH = 256
_BORDER_FRACTION = 0.12
_UNIFORM_CHANNEL_MAD = 8.0
_UNIFORM_FRAME_RATIO = 0.8
_KEY_DRIFT = 16.0

# 色键：similarity 为归一化欧氏色距（0-1），blend 控制边缘半透明过渡
_COLORKEY_SIMILARITY = 0.22
_COLORKEY_BLEND = 0.08
# 色键护栏：产出实体覆盖率不足预扫估计的一半（角色被穿透）时回退 ISNet
_COVERAGE_GUARD_RATIO = 0.5

# ISNet（DIS 通用分割，与 rembg isnet-general-use 同源权重）
_ISNET_INPUT_SIZE = 1024
_ISNET_MEAN = (0.5, 0.5, 0.5)
_ISNET_STD = (1.0, 1.0, 1.0)


@dataclass(frozen=True)
class BackgroundScan:
    """不透明源的低分辨率预扫结果。"""

    uniform: bool
    key_color: tuple[int, int, int] | None
    # 有角色帧的实体像素占比中位数（与色键产出覆盖率对照的护栏基线）
    subject_fraction: float


@dataclass(frozen=True)
class MatteResult:
    method: str  # chromakey | isnet


def _scan_frames(src: Path):
    """低分辨率逐帧 RGB 解码；返回 (帧数组 [N,h,w,3], 高, 宽)。"""
    import numpy as np

    probe = probe_video(src)
    width = _SCAN_WIDTH
    height = max(2, 2 * round(probe.height * width / probe.width / 2))
    args = [
        "-i",
        str(src),
        "-vf",
        f"scale={width}:{height}:flags=area",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = _run([_binary("ffmpeg"), "-v", "error", *args])
    if proc.returncode != 0:
        raise VideoProcessError("背景扫描失败", internal=proc.stderr.decode("utf-8", "replace")[:2000])
    frame_size = width * height * 3
    raw = np.frombuffer(proc.stdout, dtype=np.uint8)
    frame_count = len(raw) // frame_size
    if frame_count == 0:
        raise VideoProcessError("背景扫描失败", internal="no frames decoded")
    frames = raw[: frame_count * frame_size].reshape(frame_count, height, width, 3)
    return frames, height, width


def scan_background(src: Path) -> BackgroundScan:
    """边缘带采样：多数帧边缘为纯色（MAD 小）且中位色稳定即可色键；同时估计实体占比基线。

    逐帧判据用边框像素的中位绝对偏差（MAD）而非方差：角色跳入跳出短暂贴边时中位数统计
    对其鲁棒，而纹理/噪声背景的 MAD 与方差同样大；跨帧再用中位色漂移排除缓变背景。
    numpy 与 onnxruntime 为重运行时，按需加载（色键路径不需要 onnxruntime）。
    """
    import numpy as np

    frames, _height, _width = _scan_frames(src)
    border = _border_mask(frames.shape[1], frames.shape[2])

    border_medians = np.stack([np.median(frame[border], axis=0) for frame in frames])
    key = np.median(border_medians, axis=0)
    drift_ok = np.abs(border_medians - key).max(axis=1) <= _KEY_DRIFT
    solid_ok = (
        np.stack(
            [
                np.median(np.abs(frame[border].astype(np.float32) - np.median(frame[border], axis=0)), axis=0)
                for frame in frames
            ],
        ).max(axis=1)
        <= _UNIFORM_CHANNEL_MAD
    )
    if float((drift_ok & solid_ok).mean()) < _UNIFORM_FRAME_RATIO:
        return BackgroundScan(uniform=False, key_color=None, subject_fraction=0.0)

    # 与 colorkey 相同量纲的欧氏色距（0-255 尺度）；实体 = 距键色超过 similarity 一半的像素
    distance_cut = 0.5 * _COLORKEY_SIMILARITY * (255.0 * (3.0**0.5))
    subject_fractions = [
        float((np.linalg.norm(frame.astype(np.float32) - key, axis=-1) > distance_cut).mean()) for frame in frames
    ]
    present = [fraction for fraction in subject_fractions if fraction > 0.02]
    subject_fraction = statistics.median(present) if present else 0.0
    key_color = tuple(int(channel) for channel in key)
    return BackgroundScan(uniform=True, key_color=key_color, subject_fraction=subject_fraction)


def _border_mask(height: int, width: int):
    import numpy as np

    border = np.zeros((height, width), dtype=bool)
    band_h = max(1, int(height * _BORDER_FRACTION))
    band_w = max(1, int(width * _BORDER_FRACTION))
    border[:band_h, :] = True
    border[-band_h:, :] = True
    border[:, :band_w] = True
    border[:, -band_w:] = True
    return border


def _encode_with_vf(src: Path, dst: Path, vf: str) -> None:
    args = ["-i", str(src), "-an", "-sn", "-dn", "-vf", vf, *_FFV1_ALPHA_ARGS, str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, label="透明化")


def _chromakey(src: Path, dst: Path, key_color: tuple[int, int, int]) -> None:
    hexcolor = "0x{:02x}{:02x}{:02x}".format(*key_color)
    vf = f"colorkey={hexcolor}:{_COLORKEY_SIMILARITY}:{_COLORKEY_BLEND}"
    _encode_with_vf(src, dst, vf)


def _require_alpha(dst: Path) -> None:
    probe = probe_video(dst)
    if probe.codec_name != "ffv1" or not probe.has_pixel_alpha:
        raise VideoProcessError("透明化产物缺少透明通道", internal=f"codec={probe.codec_name} pix={probe.pix_fmt}")


def _chromakey_coverage_guard(dst: Path, baseline: float) -> bool:
    """色键产出的实体覆盖率是否达标；不足说明角色被色键穿透，应回退模型抠像。"""
    if baseline <= 0.02:
        return True
    coverage, _fps = alpha_coverage_series(dst)
    present = [value for value in coverage if value > 0.02]
    if not present:
        return False
    return statistics.median(present) >= _COVERAGE_GUARD_RATIO * baseline


def _isnet_matte(src: Path, dst: Path, model_path: Path) -> None:
    """ISNet 逐帧抠像：抽帧 → onnx 前向 → alpha 合成 RGBA PNG → FFV1/MKV 编码。"""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    probe = probe_video(src)
    session = ort.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
        sess_options=ort.SessionOptions(),
    )
    input_name = session.get_inputs()[0].name

    with tempfile.TemporaryDirectory(prefix="isnet-") as tmp:
        work = Path(tmp)
        frames_dir = work / "frames"
        frames_dir.mkdir()
        run_ffmpeg(["-i", str(src), str(frames_dir / "f%05d.png")], label="抽帧")

        matte_dir = work / "matte"
        matte_dir.mkdir()
        frame_paths = sorted(frames_dir.glob("f*.png"))
        if not frame_paths:
            raise VideoProcessError("透明化抽帧失败", internal="no frames extracted")

        mean = np.array(_ISNET_MEAN, dtype=np.float32)
        std = np.array(_ISNET_STD, dtype=np.float32)
        for index, frame_path in enumerate(frame_paths):
            image = Image.open(frame_path).convert("RGB")
            original_size = image.size
            tensor = image.resize((_ISNET_INPUT_SIZE, _ISNET_INPUT_SIZE), Image.BILINEAR)
            array = np.asarray(tensor, dtype=np.float32) / 255.0
            array = (array - mean) / std
            array = array.transpose(2, 0, 1)[None]

            output = session.run(None, {input_name: array})[0]
            mask = np.squeeze(output).astype(np.float32)
            span = float(mask.max() - mask.min())
            if span > 0:
                mask = (mask - mask.min()) / span
            alpha = Image.fromarray((mask * 255).astype(np.uint8)).resize(original_size, Image.BILINEAR)

            rgba = image.convert("RGBA")
            rgba.putalpha(alpha)
            rgba.save(matte_dir / frame_path.name)
            if (index + 1) % 50 == 0:
                logger.info("isnet matting progress", extra={"done": index + 1, "total": len(frame_paths)})

        args = [
            "-framerate",
            f"{probe.fps:.6f}",
            "-i",
            str(matte_dir / "f%05d.png"),
            "-an",
            "-sn",
            "-dn",
            *_FFV1_ALPHA_ARGS,
            str(dst),
        ]
        dst.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(args, label="透明化")


def matte_video(src: Path, dst: Path) -> MatteResult:
    """不透明源 → 带 alpha 中间视频：色键优先，护栏或失败时回退 ISNet。"""
    scan = scan_background(src)
    if scan.uniform and scan.key_color is not None:
        try:
            _chromakey(src, dst, scan.key_color)
            _require_alpha(dst)
            if _chromakey_coverage_guard(dst, scan.subject_fraction):
                logger.info("matte via chromakey", extra={"key": scan.key_color})
                return MatteResult(method="chromakey")
            logger.info(
                "chromakey coverage guard failed; falling back to isnet",
                extra={"baseline": scan.subject_fraction},
            )
        except VideoProcessError:
            logger.warning("chromakey matte failed; falling back to isnet", exc_info=True)

    model_path = Path(SETTINGS.data_dir) / "models" / f"{SETTINGS.matting_model}.onnx"
    if not model_path.exists():
        raise VideoProcessError(
            "视频背景不是纯色且抠像模型未就绪，请联系管理员配置后重试",
            internal=f"missing matting model {model_path}",
        )
    _isnet_matte(src, dst, model_path)
    _require_alpha(dst)
    return MatteResult(method="isnet")
