"""对生成图片做独立的可见身份核查；不改写候选图。"""

import asyncio
import json
from collections.abc import Awaitable, Callable

from components import SETTINGS, get_logger, parse_llm_json
from prompts.generation import (
    CHARACTER_ACTION_VIDEO_REVIEW,
    CHARACTER_MEDIA_IMAGE_SCORE,
    CHARACTER_MEDIA_VIDEO_SCORE,
    CHARACTER_VIDEO_PACK_REVIEW,
)

from services.infrastructure.assets import unlink_companion_asset
from services.infrastructure.llm import vision_chat

from .avatar_service import load_avatar_bytes_as_data_uri

logger = get_logger(__name__)
MEDIA_IDENTITY_ACCEPT_SCORE = 75


async def _score_media(user_id: int, prompt: str, images: tuple[str, ...]) -> int | None:
    if not images or any(not image for image in images):
        return None
    try:
        raw = await vision_chat(
            user_id,
            prompt,
            json.dumps({"image_count": len(images)}),
            reference_images=images,
        )
        payload = parse_llm_json(raw)
        score = payload.get("score") if isinstance(payload, dict) else None
        if type(score) is int and 0 <= score <= 100:
            return score
    except Exception:
        logger.warning("character media score failed", extra={"user_id": user_id}, exc_info=True)
    return None


async def score_character_image(user_id: int, identity_uri: str | None, candidate_uri: str) -> int | None:
    return await _score_media(user_id, CHARACTER_MEDIA_IMAGE_SCORE, (identity_uri or "", candidate_uri))


async def score_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
) -> int | None:
    return await _score_media(user_id, CHARACTER_MEDIA_VIDEO_SCORE, (identity_uri or "", *frame_uris))


async def select_best_character_images(
    user_id: int,
    identity_uri: str,
    initial_urls: list[str],
    regenerate: Callable[[], Awaitable[str]],
) -> list[str]:
    """日常图片的已知候选至多重绘配置次数；始终返回评分最高的已知产物。"""
    selected: list[str] = []
    max_retries = SETTINGS.character_media_regeneration_max_retries
    for first_url in initial_urls:
        best_url = first_url
        best_score = -1
        candidates = [first_url]
        for attempt in range(max_retries + 1):
            if attempt:
                try:
                    candidate = await regenerate()
                except Exception:
                    logger.warning("character image regeneration stopped; keeping best known asset", exc_info=True)
                    break
                candidates.append(candidate)
            else:
                candidate = first_url
            candidate_uri = await asyncio.to_thread(load_avatar_bytes_as_data_uri, candidate)
            score = await score_character_image(user_id, identity_uri, candidate_uri or "")
            if score is None:
                # 评分服务不可用时，不为不确定性继续付费；保留已有的最高分或首张有效结果。
                break
            if score > best_score:
                best_url, best_score = candidate, score
            if score >= MEDIA_IDENTITY_ACCEPT_SCORE:
                break
        selected.append(best_url)
        for candidate in candidates:
            if candidate != best_url:
                await asyncio.to_thread(unlink_companion_asset, candidate)
    return selected


async def review_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    pack_wide: bool = False,
) -> tuple[str, str]:
    if not identity_uri or not frame_uris:
        return "review", "参考形象或视频画面无法读取，请预览确认"
    try:
        raw = await vision_chat(
            user_id,
            CHARACTER_VIDEO_PACK_REVIEW if pack_wide else CHARACTER_ACTION_VIDEO_REVIEW,
            json.dumps({"frame_count": len(frame_uris)}),
            reference_images=(identity_uri, *frame_uris),
        )
        payload = parse_llm_json(raw)
        if isinstance(payload, dict) and payload.get("verdict") in ("pass", "review"):
            reason = str(payload.get("reason") or "").strip()[:500]
            return payload["verdict"], reason or "角色外形可能有变化，请预览确认"
    except Exception:
        logger.warning("character video review failed", extra={"user_id": user_id}, exc_info=True)
    return "review", "自动检查未完成，请预览确认"
