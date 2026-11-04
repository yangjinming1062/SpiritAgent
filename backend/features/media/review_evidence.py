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


async def freeze_review_evidence(
    user_id: int, images: tuple[str, ...], *, prompt: str, context: object, media_path: str | None = None
) -> ReviewEvidence:
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
        entries.append(ReviewImage(path=path, sha256=digest, role=f"image_{index + 1}"))
    resolved = resolve_asset_reference(media_path) if media_path else None
    if resolved is not None:
        media_hash = await asyncio.to_thread(lambda: hashlib.sha256(resolved[0].read_bytes()).hexdigest())
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
def _file_hash(path: str, size: int, modified_ns: int) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset_file_hash(reference: str) -> str | None:
    try:
        resolved = resolve_asset_reference(reference)
        if resolved is None:
            return None
        stat = resolved[0].stat()
        return _file_hash(str(resolved[0]), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


def review_evidence_intact(evidence: ReviewEvidence) -> bool:
    if hashlib.sha256(evidence.policy_prompt.encode()).hexdigest() != evidence.policy_hash:
        return False
    try:
        for entry in evidence.images:
            resolved = resolve_asset_reference(entry.path)
            if resolved is None:
                return False
            path = resolved[0]
            stat = path.stat()
            if _file_hash(str(path), stat.st_size, stat.st_mtime_ns) != entry.sha256:
                return False
    except OSError:
        return False
    return True


def review_media_intact(evidence: ReviewEvidence, media_path: str) -> bool:
    if not review_evidence_intact(evidence):
        return False
    try:
        resolved = resolve_asset_reference(media_path)
        if resolved is None:
            return False
        path = resolved[0]
        stat = path.stat()
        return evidence.media_hash == _file_hash(str(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return False
