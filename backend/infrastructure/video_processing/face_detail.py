"""从视频原始抽样中定位单一清晰面部，供身份复核查看局部细节。"""

from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont, ImageOps

from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.video_processing.ffmpeg import VideoProcessError, ffmpeg_stdout

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


def _face_box(
    image: Image.Image, session: ort.InferenceSession, *, strict_single: bool = False
) -> tuple[int, int, int, int] | None:
    resized = image.convert("RGB").resize((640, 480), Image.Resampling.BILINEAR)
    rgb = (np.asarray(resized, dtype=np.float32) - 127) / 128

    def detect(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        scores, bounds = session.run(["scores", "boxes"], {"input": pixels.transpose(2, 0, 1)[None]})
        confident = scores[0, :, 1] >= 0.9
        return scores[0, confident, 1], np.clip(bounds[0, confident], 0, 1)

    candidates, boxes = detect(rgb)
    if strict_single and not len(boxes):
        # 同一真实帧的另一方向可识别侧脸。原方向检出多脸时仍保留歧义。
        candidates, boxes = detect(rgb[:, ::-1])
        boxes[:, [0, 2]] = 1 - boxes[:, [2, 0]]
    if not len(boxes):
        return None
    best = boxes[int(candidates.argmax())]
    left, top, right, bottom = best
    width, height = right - left, bottom - top
    if width * image.width < 20 or height * image.height < 24:
        return None
    # 同一张脸的候选框重叠。画面出现另一张高置信度面部时保留全帧，由复核模型判断主体。
    if strict_single:
        overlap = np.maximum(0, np.minimum(boxes[:, 2:], best[2:]) - np.maximum(boxes[:, :2], best[:2])).prod(axis=1)
        union = (boxes[:, 2:] - boxes[:, :2]).prod(axis=1) + width * height - overlap
        if (overlap < 0.3 * union).any():
            return None
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
        session = _session(path, path.stat().st_mtime_ns)
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


def _native_face_crop(
    source: Path,
    index: int,
    size: tuple[int, int],
    box: tuple[int, int, int, int],
    background: tuple[int, int, int] | None,
    decoder_args: tuple[str, ...],
    with_alpha: bool,
) -> Image.Image:
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    pixels = "rgba" if with_alpha else "rgb24"
    data = ffmpeg_stdout(
        [
            *decoder_args,
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"select=eq(n\\,{index}),scale={size[0]}:{size[1]},setsar=1,format={pixels},crop={w}:{h}:{left}:{top}:exact=1",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pixels,
            "-",
        ],
        failure_message="视频面部局部解码失败",
    )
    patch = Image.frombytes("RGBA" if with_alpha else "RGB", (w, h), data)
    if background is None:
        return patch
    composite = Image.new("RGB", patch.size, background)
    with patch:
        composite.paste(patch, mask=patch.getchannel("A"))
    return composite


def review_face_details(
    frames: tuple[bytes, ...],
    frame_times: tuple[float, ...],
    *,
    source: Path | None = None,
    frame_indices: tuple[int, ...] = (),
    display_size: tuple[int, int] | None = None,
    background: tuple[int, int, int] | None = None,
    decoder_args: tuple[str, ...] = (),
) -> tuple[tuple[float, bytes], ...]:
    path = review_model_path(_MODEL)
    if not frames or not path.is_file():
        return ()
    details: list[tuple[float, bytes]] = []
    try:
        session = _session(path, path.stat().st_mtime_ns)
        boxes = {}
        patches = {}
        for index, data in enumerate(frames):
            with Image.open(BytesIO(data)) as image:
                box = _face_box(image, session)
                if box is not None:
                    boxes[index] = box
                    with image.crop(box) as face:
                        patches[index] = np.asarray(face.convert("L").resize((32, 32)), dtype=np.int16)
        if not boxes:
            return ()
        # 保留首尾与均匀时刻，再补面部变化最大的一对相邻采样；变化量只选择证据。
        selected = {
            min(boxes, key=lambda i: abs(frame_times[i] - target))
            for target in np.linspace(frame_times[0], frame_times[-1], 9)
        }
        adjacent = [i for i in patches if i - 1 in patches]
        if adjacent:
            change = max(adjacent, key=lambda i: np.abs(patches[i] - patches[i - 1]).mean())
            selected.update((change - 1, change))
        for index in sorted(selected):
            with Image.open(BytesIO(frames[index])) as image:
                box = boxes[index]
                if source is not None and index < len(frame_indices) and display_size and display_size != image.size:
                    sx, sy = display_size[0] / image.width, display_size[1] / image.height
                    x, y, r, b = box
                    native = (round(x * sx), round(y * sy), round(r * sx), round(b * sy))
                    try:
                        face = _native_face_crop(
                            source,
                            frame_indices[index],
                            display_size,
                            native,
                            background,
                            decoder_args,
                            with_alpha=background is not None or image.mode == "RGBA",
                        )
                    except (VideoProcessError, OSError, ValueError):
                        logger.warning("Using sampled pixels for an unavailable native face crop")
                        face = image.crop(box)
                else:
                    face = image.crop(box)
            with face, BytesIO() as output:
                face.save(output, format="WEBP", lossless=True)
                details.append((frame_times[index], output.getvalue()))
    except Exception:
        logger.warning("Video face detail unavailable", exc_info=True)
    return tuple(details)


def _face_rows(rows: list[list[tuple[str, bytes]]]) -> bytes:
    width, height = 202, 229
    columns = max(len(row) for row in rows)
    with Image.new("RGB", (columns * width, len(rows) * (height + 24)), "#eeeeeb") as sheet:
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default(size=15)
        for row, samples in enumerate(rows):
            y = row * (height + 24)
            for column, (label, pixels) in enumerate(samples):
                x = column * width
                with Image.open(BytesIO(pixels)) as region:
                    # 定位框有外缘，保留面容和遮挡、按原宽高比显示。
                    dx, dy = round(region.width * 0.075), round(region.height * 0.045)
                    with (
                        region.crop((dx, dy, region.width - dx, region.height - dy)) as face,
                        ImageOps.contain(face, (width - 4, height), Image.Resampling.LANCZOS) as tile,
                    ):
                        at = (x + (width - tile.width) // 2, y + 22 + (height - tile.height) // 2)
                        sheet.paste(tile, at, tile.getchannel("A") if tile.mode == "RGBA" else None)
                draw.text((x + 7, y + 1), label, fill="#242526", font=font)
        with BytesIO() as output:
            sheet.save(output, format="WEBP", lossless=True)
            return output.getvalue()


def face_sequence_sheet(
    details: tuple[tuple[float, bytes], ...], reference: bytes
) -> tuple[tuple[float, ...], bytes] | None:
    """同屏比较原参考与同一成片的连续局部；R 始终为原参考。"""
    model = review_model_path(_MODEL)
    if len(details) < 2 or details[0][0] >= details[-1][0] or not model.is_file():
        return None
    try:
        with Image.open(BytesIO(reference)) as image, BytesIO() as encoded:
            ImageOps.exif_transpose(image, in_place=True)
            box = _face_box(image, _session(model, model.stat().st_mtime_ns))
            if box is None:
                return None
            with image.crop(box) as face:
                face.save(encoded, format="WEBP", lossless=True)
            original = ("R", encoded.getvalue())
        # 采样已保留最多九个均匀时刻和一对变化帧，同屏保留这两个可能很短的中间状态。
        times = tuple(time for time, _ in details)
        tiles = [(f"{n}    {time:.2f} s", pixels) for n, (time, pixels) in enumerate(details, 1)]
        return times, _face_rows([[original, *tiles[i : i + 3]] for i in range(0, len(tiles), 3)])
    except Exception:
        logger.warning("Using individual face crops for video review", exc_info=True)
        return None


def compare_face_sequences(candidates: dict[str, list[tuple[float, bytes]]]) -> list[bytes]:
    """每版用一行展示同等数量的真实局部，按实际采样时间标注。"""
    if len(candidates) < 2:
        return []
    count = min(6, min(len(details) for details in candidates.values()))
    if count < 2:
        return []
    rows = []
    for key, details in candidates.items():
        # 各版实际片长与动作速度可以不同。按时间覆盖相同进度，显示原始时刻。
        times = [time for time, _ in details]
        indices = np.linspace(0, len(details) - 1, count).round().astype(int)
        rows.append([(f"{key}   {times[i]:.2f} s", details[i][1]) for i in indices])
    try:
        return [_face_rows(rows[i : i + 3]) for i in range(0, len(rows) - 1, 2)]
    except (OSError, ValueError):
        logger.warning("Using individual face crops for candidate comparison", exc_info=True)
        return []
