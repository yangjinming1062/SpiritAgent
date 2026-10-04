"""场景候选核对是否重复描绘桌面伙伴；识别参考仅进入独立视觉检查。"""

import asyncio
import json
from collections.abc import Awaitable, Callable

from components import SESSION_LOCAL, get_logger, parse_llm_json
from modules.companion import AvatarAsset, Persona
from prompts.generation import SCENE_IMAGE_REVIEW_SYSTEM
from sqlalchemy import select

from services.infrastructure.assets import asset_store, read_asset_data_uri
from services.infrastructure.llm import LlmCallBlockedError, vision_chat

from .image_generation import ImageReviewUnavailableError

logger = get_logger(__name__)


async def _companion_reference_uri(user_id: int) -> str:
    async with SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(AvatarAsset, Persona.is_portrait_confirmed)
                .join(Persona, Persona.user_id == AvatarAsset.user_id)
                .where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)),
            )
        ).first()
    if row is not None:
        avatar, portrait_confirmed = row
        paths = (
            avatar.seed_fullbody_url if avatar.is_fullbody_confirmed else "",
            avatar.asset_url if portrait_confirmed else "",
        )
        for path in paths:
            bare = asset_store.normalize_asset_reference(path)
            parsed = asset_store.parse_companion_asset_path(bare)
            if (
                parsed is not None
                and parsed[0] == user_id
                and (uri := await asyncio.to_thread(read_asset_data_uri, bare))
            ):
                return uri
    raise ImageReviewUnavailableError("场景图片已保留，但伙伴的已确认形象无法读取；请修复形象后重试分析")


async def review_scene_image(
    user_id: int,
    candidate_uri: str,
    *,
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> tuple[bool, str]:
    try:
        companion_uri = await _companion_reference_uri(user_id)
        raw = await vision_chat(
            user_id,
            SCENE_IMAGE_REVIEW_SYSTEM,
            json.dumps({"image_count": 2}, ensure_ascii=False),
            reference_images=(companion_uri, candidate_uri),
            before_submit=before_submit,
        )
        payload = parse_llm_json(raw)
        if (
            not isinstance(payload, dict)
            or type(payload.get("accepted")) is not bool
            or not isinstance(payload.get("reason"), str)
        ):
            raise ValueError("场景内容核查返回无效结构")
        accepted, reason = payload["accepted"], payload["reason"].strip()
        logger.info("scene image reviewed", extra={"user_id": user_id, "accepted": accepted, "reason": reason[:200]})
        return accepted, reason
    except (LlmCallBlockedError, ImageReviewUnavailableError):
        raise
    except Exception as exc:
        raise ImageReviewUnavailableError("场景图片已生成，内容核查暂未完成，请重试分析", internal=str(exc)) from exc
