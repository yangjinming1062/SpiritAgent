"""场景候选核对是否重复描绘桌面伙伴；识别参考仅进入独立视觉检查。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets import asset_store
from infrastructure.assets.asset_store import read_asset_data_uri
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field

from features.media.image_generation import ImageReviewUnavailableError
from features.media.prompts import SCENE_IMAGE_REVIEW_SYSTEM
from features.media.reference_reader import confirmed_media_reference_paths

logger = get_logger(__name__)


class SceneImageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "duplicate_character", "invalid_environment"]
    reason: str = Field(min_length=1, max_length=1000)


async def _companion_reference_uri(user_id: int) -> str:
    async with SESSION_LOCAL() as db:
        paths = await confirmed_media_reference_paths(db, user_id)
    for path in paths:
        bare = asset_store.normalize_asset_reference(path)
        parsed = parse_companion_asset_path(bare)
        if parsed is not None and parsed[0] == user_id and (uri := await asyncio.to_thread(read_asset_data_uri, bare)):
            return uri
    raise ImageReviewUnavailableError("场景图片已保留，但伙伴的已确认形象无法读取；请修复形象后重试分析")


async def review_scene_image(
    user_id: int,
    candidate_uri: str,
    *,
    before_submit: Callable[[], Awaitable[None]] | None = None,
    task: AiTaskKey = AiTaskKey.scene,
) -> tuple[bool, str]:
    try:
        companion_uri = await _companion_reference_uri(user_id)
        config = await load_user_llm_config(
            user_id, task, required_modalities=frozenset({ModelModality.text, ModelModality.image})
        )
        payload = await call_llm_json(
            config,
            SCENE_IMAGE_REVIEW_SYSTEM,
            {
                "references": [
                    {"image": 1, "role": "reference_character"},
                    {"image": 2, "role": "candidate_environment"},
                ]
            },
            schema=SceneImageReview.model_json_schema(),
            schema_name="scene_image_review",
            parse_output=SceneImageReview.model_validate,
            repair_prompt="只修正已有结论的 JSON 结构或字段格式，保留 decision 与可见依据，不重新判断图像。",
            reference_images=(companion_uri, candidate_uri),
            before_call=before_submit,
            preserve_choices=("decision",),
        )
        accepted, reason = payload.decision == "accepted", payload.reason.strip()
        logger.info("scene image reviewed", extra={"user_id": user_id, "accepted": accepted, "reason": reason[:200]})
        return accepted, reason
    except (LlmCallBlockedError, ImageReviewUnavailableError):
        raise
    except Exception as exc:
        raise ImageReviewUnavailableError("场景图片已生成，内容核查暂未完成，请重试分析", internal=str(exc)) from exc
