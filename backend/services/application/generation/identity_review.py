"""对生成图片做独立的可见身份核查；不改写候选图。"""

import json
from collections.abc import Awaitable, Callable

from components import get_logger, parse_llm_json
from prompts.generation import (
    CHARACTER_ACTION_IMAGE_REVIEW,
    CHARACTER_ACTION_VIDEO_REVIEW,
    CHARACTER_MEDIA_IMAGE_SCORE,
    CHARACTER_MEDIA_VIDEO_SCORE,
    CHARACTER_VIDEO_PACK_REVIEW,
)

from services.infrastructure.llm import LlmCallBlockedError, vision_chat

from .media_chain import MEDIA_IDENTITY_ACCEPT_SCORE

logger = get_logger(__name__)


def _score_output_category(payload: object) -> str:
    """评分输出不合约的类别；日志只记类别与长度，原文只在 LLM 调试日志中。"""
    if not isinstance(payload, dict):
        return "non_json" if payload is None else "non_object"
    if "score" not in payload:
        return "missing_score"
    return "out_of_range" if type(payload["score"]) is int else "non_int"


async def _score_media(
    user_id: int,
    prompt: str,
    images: tuple[str, ...],
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    if not images or any(not image for image in images):
        logger.warning("character media score skipped: image missing", extra={"user_id": user_id})
        return None
    try:
        raw = await vision_chat(
            user_id,
            prompt.format(accept_score=MEDIA_IDENTITY_ACCEPT_SCORE),
            json.dumps({"image_count": len(images), "identity": identity_text}, ensure_ascii=False),
            reference_images=images,
            before_submit=before_submit,
        )
        payload = parse_llm_json(raw)
        score = payload.get("score") if isinstance(payload, dict) else None
        if type(score) is int and 0 <= score <= 100:
            logger.info(
                "character media scored",
                extra={"user_id": user_id, "score": score, "reason": str(payload.get("reason") or "")[:200]},
            )
            return score
        logger.warning(
            "character media score output invalid",
            extra={"user_id": user_id, "category": _score_output_category(payload), "output_chars": len(raw)},
        )
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("character media score failed", extra={"user_id": user_id}, exc_info=True)
    return None


async def score_character_image(
    user_id: int,
    identity_uri: str,
    candidate_uri: str,
    *,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    return await _score_media(
        user_id,
        CHARACTER_MEDIA_IMAGE_SCORE,
        (identity_uri, candidate_uri),
        identity_text,
        before_submit,
    )


async def score_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> int | None:
    return await _score_media(
        user_id,
        CHARACTER_MEDIA_VIDEO_SCORE,
        (identity_uri or "", *frame_uris),
        identity_text,
        before_submit,
    )


async def review_character_frames(
    user_id: int,
    identity_uri: str | None,
    frame_uris: tuple[str, ...],
    *,
    pack_wide: bool = False,
    image: bool = False,
    identity_text: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> tuple[str, str]:
    if not identity_uri or not frame_uris:
        return "review", "参考形象或动作画面无法读取，请预览确认"
    if pack_wide:
        prompt = CHARACTER_VIDEO_PACK_REVIEW
    elif image:
        prompt = CHARACTER_ACTION_IMAGE_REVIEW
    else:
        prompt = CHARACTER_ACTION_VIDEO_REVIEW
    try:
        raw = await vision_chat(
            user_id,
            prompt,
            json.dumps({"image_count": len(frame_uris) + 1, "identity": identity_text}, ensure_ascii=False),
            reference_images=(identity_uri, *frame_uris),
            before_submit=before_submit,
        )
        payload = parse_llm_json(raw)
        if isinstance(payload, dict) and payload.get("verdict") in ("pass", "review"):
            reason = str(payload.get("reason") or "").strip()[:500]
            return payload["verdict"], reason or "角色外形可能有变化，请预览确认"
        logger.warning(
            "character action review output invalid",
            extra={
                "user_id": user_id,
                "category": "non_json" if payload is None else "invalid_verdict",
                "output_chars": len(raw),
            },
        )
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("character action review failed", extra={"user_id": user_id}, exc_info=True)
    return "review", "自动检查未完成，请预览确认"
