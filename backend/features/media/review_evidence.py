"""落盘实际送检字节；续审只读取原证据，不重选参考或重采样。"""

import asyncio
import hashlib
from functools import lru_cache
from pathlib import Path

from infrastructure.ai.providers._reference import resolve_reference_bytes
from infrastructure.assets.asset_store import (
    build_data_uri,
    resolve_asset_reference,
    save_image_chain_input_asset_async,
    sniff_media_ext,
)

from .review_contracts import ReviewEvidence, ReviewImage, review_context


def review_image_labels(
    reference_count: int, candidate_count: int, times: tuple[float, ...] = (), detail_times: tuple[float, ...] = ()
) -> tuple[str, ...]:
    if reference_count < 0 or candidate_count < 1 or (times and len(times) != candidate_count):
        raise ValueError("检查图片数量与时间轴不一致")
    return (
        *(f"参考图 {index + 1}（创作依据，不属于候选画面或视频时间轴）" for index in range(reference_count)),
        *(f"候选视频全帧，时间 {time:g} 秒" for time in times),
        *(f"候选画面 {index + 1}" for index in range(candidate_count) if not times),
        *(f"候选视频面部局部，取自 {time:g} 秒全帧（同一时刻，不是新镜头）" for time in detail_times),
    )


async def freeze_review_evidence(
    user_id: int,
    images: tuple[str, ...],
    *,
    prompt: str,
    context: object,
    media_path: str | None = None,
    labels: tuple[str, ...] = (),
) -> ReviewEvidence:
    if labels and len(labels) != len(images):
        raise ValueError("检查证据与图片标签数量不一致")
    entries: list[ReviewImage] = []
    for index, uri in enumerate(images):
        data, _ = await resolve_reference_bytes(uri)
        digest = hashlib.sha256(data).hexdigest()
        path = await save_image_chain_input_asset_async(
            data,
            user_id=user_id,
            generation_id=digest[:32],
            label="review",
            ext=sniff_media_ext(data),
            directory="reviews",
        )
        entries.append(ReviewImage(path=path, sha256=digest, role=labels[index] if labels else f"image_{index + 1}"))
    if media_path:
        media_hash = await asyncio.to_thread(asset_file_hash, media_path)
    else:
        media_hash = entries[-1].sha256 if entries else None
    return ReviewEvidence(
        media_hash=media_hash,
        policy_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        policy_prompt=prompt,
        context_json=review_context(context),
        images=tuple(entries),
    )


def _read_image(entry: ReviewImage) -> str:
    resolved = resolve_asset_reference(entry.path)
    if resolved is None:
        raise ValueError("检查证据无法读取")
    data = resolved[0].read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ValueError("检查证据已变化")
    return build_data_uri(data, resolved[1])


async def read_review_evidence(evidence: ReviewEvidence) -> tuple[str, ...]:
    if hashlib.sha256(evidence.policy_prompt.encode()).hexdigest() != evidence.policy_hash:
        raise ValueError("检查策略依据已变化")
    return tuple([await asyncio.to_thread(_read_image, entry) for entry in evidence.images])


@lru_cache(maxsize=2048)
def _file_hash(path: Path, version: tuple[int, ...]) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset_file_hash(reference: str) -> str | None:
    try:
        resolved = resolve_asset_reference(reference)
        if resolved is None:
            return None
        path = resolved[0]
        stat = path.stat()
        return _file_hash(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    except OSError:
        return None


def review_evidence_intact(evidence: ReviewEvidence) -> bool:
    if hashlib.sha256(evidence.policy_prompt.encode()).hexdigest() != evidence.policy_hash:
        return False
    return all(asset_file_hash(entry.path) == entry.sha256 for entry in evidence.images)


def review_media_intact(evidence: ReviewEvidence, media_path: str) -> bool:
    return (
        evidence.media_hash is not None
        and review_evidence_intact(evidence)
        and asset_file_hash(media_path) == evidence.media_hash
    )
