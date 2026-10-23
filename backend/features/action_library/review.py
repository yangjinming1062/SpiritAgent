"""动作素材的独立语义评审执行。

评审依据为角色身体能力与动作含义；评审中断转为暂缓，不自动重新付费。
"""

import asyncio
from typing import Any

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.asset_store import build_data_uri
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR
from sqlalchemy import select

from features.appearance.avatar_service import read_portrait_bytes
from features.configuration.settings.values import get_user_setting

from .models import ActionDefinition
from .prompts import ACTION_DEFINITION_REVIEW, ACTION_DEFINITION_REVIEW_REPAIR
from .queries import action_detail
from .schemas import ActionReviewSource
from .service import begin_review, record_review

logger = get_logger(__name__)
_REVIEW_LOCKS: dict[int, asyncio.Lock] = {}
_REVIEW_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}


def schedule_action_review(user_id: int, action_id: int) -> None:
    key = (user_id, action_id)
    existing = _REVIEW_TASKS.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(review_action(user_id, action_id), name=f"action.review.{action_id}")
    _REVIEW_TASKS[key] = task
    TASK_SUPERVISOR.register(task, owner="action_library.review", user_id=user_id)
    task.add_done_callback(
        lambda finished: _REVIEW_TASKS.pop(key, None) if _REVIEW_TASKS.get(key) is finished else None,
    )


class ActionReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: str = Field(pattern=r"^(approve|defer|reject)$")
    reason: str = Field(min_length=1, max_length=200)


async def review_action(user_id: int, action_id: int) -> None:
    """对 pending 状态的动作素材执行一次语义评审。"""

    async with _REVIEW_LOCKS.setdefault(user_id, asyncio.Lock()):
        try:
            await _review_action_locked(user_id, action_id)
        except BaseException:
            async with SESSION_LOCAL() as db:
                await record_review(db, user_id, action_id, decision="defer", reason="评审未完成，请显式重试")
                await db.commit()
            raise


async def _review_action_locked(user_id: int, action_id: int) -> None:
    async with SESSION_LOCAL() as db:
        current = await action_detail(db, user_id, action_id)
    if current is None or current.status != "pending":
        return

    async def before_call() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            latest = await action_detail(db, user_id, action_id)
            if latest is None or latest.status != "reviewing":
                raise LlmCallBlockedError("动作评审状态已变化")

    def parse_output(value: Any) -> ActionReviewVerdict:
        verdict = ActionReviewVerdict.model_validate(value)
        if not verdict.reason.strip():
            raise LlmJsonValidationError("empty_reason", fields=("reason",))
        return verdict

    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(ActionDefinition).where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id),
        )
        review_source_json = row.review_source_json if row is not None else "{}"
    try:
        source = ActionReviewSource.model_validate_json(review_source_json or "{}")
    except ValueError:
        source = None
    references: list[str] = []
    if source is not None:
        for path in (source.identity_path, source.portrait_path):
            loaded = await asyncio.to_thread(read_portrait_bytes, path)
            if loaded is not None:
                references.append(await asyncio.to_thread(build_data_uri, loaded[0], loaded[1]))
    if not references:
        raise ValueError("动作评审缺少可读的冻结身份参考图")

    config = await load_user_llm_config(
        user_id,
        AiTaskKey.visuals,
        required_modalities=frozenset({ModelModality.text, ModelModality.image}),
    )
    async with SESSION_LOCAL() as db:
        language = resolve_language(await get_user_setting(db, user_id, "language"))
    payload = {
        "output_language": language,
        "design": current.model_dump(
            include={"name", "motion_description", "use_when", "avoid_when", "clip_kind", "duration_seconds"},
        ),
        "reason": current.reason,
        "identity": source.identity.model_dump() if source else None,
        "persona": source.persona if source else {},
    }
    async with SESSION_LOCAL() as db:
        if not await begin_review(db, user_id, action_id, expected_updated_at=current.updated_at):
            return
        await db.commit()
    verdict = await call_llm_json(
        config,
        ACTION_DEFINITION_REVIEW,
        payload,
        schema=ActionReviewVerdict.model_json_schema(),
        schema_name="action_asset_review",
        parse_output=parse_output,
        repair_prompt=ACTION_DEFINITION_REVIEW_REPAIR,
        reference_images=tuple(references),
        preserve_choices=("decision",),
        before_call=before_call,
    )
    async with SESSION_LOCAL() as db:
        await record_review(
            db,
            user_id,
            action_id,
            decision=verdict.decision,
            reason=verdict.reason,
        )
        await db.commit()
