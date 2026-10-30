"""原件采样、实际像素提取和轻量视觉检索；不生成或补画内容。"""

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from infrastructure.assets.asset_store import resolve_asset_reference, save_companion_asset_async
from infrastructure.async_io import run_blocking
from infrastructure.video_processing.actor_clip import prepare_actor_clip
from infrastructure.video_processing.ffmpeg import alpha_input_args, ffmpeg_stdout, probe_video
from PIL import Image, ImageOps

from .schemas import ActorClipBody, VisualAsset


@dataclass(frozen=True, slots=True)
class ImportFrame:
    seconds: float
    data: bytes


@dataclass(frozen=True, slots=True)
class ImportSamples:
    frames: list[ImportFrame]
    animated_image: bool = False
    duration_ms: int | None = None
    timing_reliable: bool = True


def image_frame(data: bytes, *, max_edge: int = 1280) -> bytes:
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail((max_edge, max_edge))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def video_frame(path: Path, seconds: float) -> bytes:
    probe = probe_video(path)
    return ffmpeg_stdout(
        [
            "-ss",
            f"{max(0.0, seconds):.3f}",
            *alpha_input_args(probe),
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "scale=1280:1280:force_original_aspect_ratio=decrease",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        failure_message="上传视频抽帧失败",
    )


def sample_import(reference: str, media_kind: str, duration_ms: int | None) -> ImportSamples:
    resolved = resolve_asset_reference(reference)
    if resolved is None:
        raise ValueError("上传原件缺失")
    if media_kind == "image":
        with Image.open(resolved[0]) as image:
            count = getattr(image, "n_frames", 1)
            if count <= 1:
                return ImportSamples([ImportFrame(0, image_frame(resolved[0].read_bytes()))])
            selected = set(np.linspace(0, count - 1, min(24, count), dtype=int))
            frames = []
            elapsed = 0
            reliable = True
            for index in range(count):
                image.seek(index)
                image.load()
                if index in selected:
                    output = io.BytesIO()
                    image.convert("RGBA").save(output, format="PNG")
                    frames.append(ImportFrame(elapsed / 1000, image_frame(output.getvalue())))
                duration = int(image.info.get("duration") or 0)
                reliable = reliable and duration > 0
                elapsed += duration
            return ImportSamples(frames, True, elapsed if reliable else None, reliable)
    duration = (duration_ms or 0) / 1000
    count = min(24, max(3, round(duration * 2) + 1))
    times = np.linspace(0, max(0, duration - min(0.15, duration / 10)), count)
    return ImportSamples(
        [ImportFrame(float(time), video_frame(resolved[0], float(time))) for time in times], duration_ms=duration_ms
    )


def extract_region(data: bytes, crop: tuple[float, float, float, float] | None, *, min_edge: int = 64) -> bytes:
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        if crop is not None:
            left, top, right, bottom = crop
            image = image.crop(
                (
                    round(left * image.width),
                    round(top * image.height),
                    round(right * image.width),
                    round(bottom * image.height),
                )
            )
        if min(image.size) < min_edge:
            raise ValueError("提取区域过小，无法成为可复用素材")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def visual_vector_bytes(data: bytes) -> np.ndarray | None:
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((96, 96))
            image = image.convert("RGB").resize((24, 24))
            pixels = np.asarray(image, dtype=np.float32) / 255
            histogram = np.concatenate(
                [np.histogram(pixels[..., channel], bins=12, range=(0, 1), density=True)[0] for channel in range(3)]
            )
            spatial = pixels.reshape(6, 4, 6, 4, 3).mean(axis=(1, 3)).ravel()
            vector = np.concatenate([histogram / 12, spatial])
            return vector / max(float(np.linalg.norm(vector)), 1e-8)
    except (OSError, ValueError):
        return None


def visual_vector(reference: str) -> np.ndarray | None:
    resolved = resolve_asset_reference(reference)
    return visual_vector_bytes(resolved[0].read_bytes()) if resolved else None


def retrieval_score(description: str, query: str, reference: str | None, target: np.ndarray | None) -> float:
    def terms(value: str) -> set[str]:
        text = value.lower()
        words = set(re.findall(r"[a-z0-9]+", text))
        for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
            words.update(phrase[index : index + 2] for index in range(max(1, len(phrase) - 1)))
        return words

    words, wanted = terms(description), terms(query)
    semantic = len(words & wanted) / max(1, len(wanted))
    vector = visual_vector(reference) if reference else None
    visual = float(np.dot(vector, target)) if vector is not None and target is not None else 0.0
    return semantic * 0.65 + visual * 0.35


async def prepare_imported_actor(reference: str, user_id: int, job_id: int) -> VisualAsset:
    resolved = resolve_asset_reference(reference)
    if resolved is None:
        raise ValueError("原始主体视频缺失")
    directory = f"visuals/analysis/{job_id}"
    with TemporaryDirectory(prefix="import-actor-", dir=resolved[0].parent) as temporary:
        root = Path(temporary)
        media = await run_blocking(
            prepare_actor_clip,
            resolved[0],
            dst=root / "clip.webm",
            poster_dst=root / "poster.webp",
            mask_dst=root / "mask.json",
            first_frame_dst=root / "first.png",
            last_frame_dst=root / "last.png",
            preserve_alpha=True,
        )
        paths = {}
        for role, path in (
            ("video", media.video_path),
            ("poster", media.poster_path),
            ("mask", media.mask_path),
            ("first", media.first_frame_path),
            ("last", media.last_frame_path),
        ):
            data = await run_blocking(path.read_bytes)
            paths[role] = await save_companion_asset_async(
                data, user_id=user_id, label=f"actor_{role}", ext=path.suffix[1:], directory=directory
            )
        first = await run_blocking(media.first_frame_path.read_bytes)
    return VisualAsset(
        media_kind="video",
        media_path=paths["video"],
        poster_path=paths["poster"],
        width=media.width,
        height=media.height,
        duration_ms=media.duration_ms,
        body=ActorClipBody(
            mask_path=paths["mask"],
            first_frame_path=paths["first"],
            last_frame_path=paths["last"],
            foot_anchor=media.foot_anchor,
            body_bounds=media.body_bounds,
            hit_bounds=media.hit_bounds,
            continuity_key=hashlib.sha256(first).hexdigest(),
        ),
    )
