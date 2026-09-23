"""对生成图片做独立的可见身份核查；不改写候选图。"""

import json

from components import get_logger, parse_llm_json
from prompts.generation import (
    CHARACTER_ACTION_VIDEO_REVIEW,
    CHARACTER_MEDIA_IMAGE_SCORE,
    CHARACTER_MEDIA_VIDEO_SCORE,
    CHARACTER_VIDEO_PACK_REVIEW,
)

from services.infrastructure.llm import vision_chat

from .media_chain import MEDIA_IDENTITY_ACCEPT_SCORE

logger = get_logger(__name__)


async def _score_media(
    user_id: int,
    prompt: str,
    images: tuple[str, ...],
    identity_text: str = "",
) -> int | None:
    if not images or any(not image for image in images):
        return None
    try:
        raw = await vision_chat(
            user_id,
            prompt.format(accept_score=MEDIA_IDENTITY_ACCEPT_SCORE),
            json.dumps({"image_count": len(images), "identity": identity_text}, ensure_ascii=False),
            reference_images=images,
        )
        payload = parse_llm_json(raw)
        score = payload.get("score") if isinstance(payload, dict) else None
        if type(score) is int and 0 <= score <= 100:
            return score
    except Exception:
        logger.warning("character media score failed", extra={"user_id": user_id}, exc_info=True)
    return None


async def score_character_image(
    user_id: int,
    identity_uri: str | None,
    candidate_uri: str,
    *,
    identity_text: str = "",
) -> int | None:
    return await _score_media(user_id, CHARACTER_MEDIA_IMAGE_SCORE, (identity_uri or "", candidate_uri), identity_text)


async def score_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    identity_text: str = "",
) -> int | None:
    return await _score_media(user_id, CHARACTER_MEDIA_VIDEO_SCORE, (identity_uri or "", *frame_uris), identity_text)


async def review_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    pack_wide: bool = False,
    identity_text: str = "",
) -> tuple[str, str]:
    if not identity_uri or not frame_uris:
        return "review", "参考形象或视频画面无法读取，请预览确认"
    try:
        raw = await vision_chat(
            user_id,
            CHARACTER_VIDEO_PACK_REVIEW if pack_wide else CHARACTER_ACTION_VIDEO_REVIEW,
            json.dumps({"frame_count": len(frame_uris), "identity": identity_text}, ensure_ascii=False),
            reference_images=(identity_uri, *frame_uris),
        )
        payload = parse_llm_json(raw)
        if isinstance(payload, dict) and payload.get("verdict") in ("pass", "review"):
            reason = str(payload.get("reason") or "").strip()[:500]
            return payload["verdict"], reason or "角色外形可能有变化，请预览确认"
    except Exception:
        logger.warning("character video review failed", extra={"user_id": user_id}, exc_info=True)
    return "review", "自动检查未完成，请预览确认"
