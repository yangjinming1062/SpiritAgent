"""场景候选核对是否重复描绘桌面伙伴；识别参考仅进入独立视觉检查。"""

import asyncio
import json
from collections.abc import Awaitable, Callable

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import vision_chat
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import read_asset_data_uri
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json

from features.media.image_generation import ImageReviewUnavailableError
from features.media.prompts import SCENE_IMAGE_REVIEW_SYSTEM
from features.media.reference_reader import confirmed_media_reference_paths

logger = get_logger(__name__)


async def _companion_reference_uri(user_id: int) -> str:
    async with SESSION_LOCAL() as db:
        paths = await confirmed_media_reference_paths(db, user_id)
    for path in paths:
        bare = asset_store.normalize_asset_reference(path)
        parsed = asset_store.parse_companion_asset_path(bare)
        if parsed is not None and parsed[0] == user_id and (uri := await asyncio.to_thread(read_asset_data_uri, bare)):
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
            task=AiTaskKey.scene,
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
