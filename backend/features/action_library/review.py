"""动作语义评审；冻结修订，区分模型结论和技术失败，不自动重复付费。"""

import asyncio
from datetime import datetime
from typing import Literal

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.models import ModelModality
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.asset_store import build_data_uri
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import ensure_utc, resolve_language
from pydantic import BaseModel, ConfigDict, Field
from runtime.paid_work import require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR

from features.appearance.avatar_service import read_portrait_bytes
from features.configuration.settings.values import get_user_setting

from .prompts import ACTION_DEFINITION_REVIEW, ACTION_DEFINITION_REVIEW_REPAIR
from .queries import action_detail
from .schemas import ActionAssetView, ActionReviewSource
from .service import begin_review, record_review

logger = get_logger(__name__)
_REVIEW_LOCKS: dict[int, asyncio.Lock] = {}
_REVIEW_TASKS: dict[tuple[int, int, datetime], asyncio.Task[None]] = {}


class ActionReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "defer", "reject"]
    reason: str = Field(min_length=1, max_length=200)


def schedule_action_review(user_id: int, action_id: int, expected_updated_at: datetime) -> None:
    key = (user_id, action_id, ensure_utc(expected_updated_at))
    existing = _REVIEW_TASKS.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(
        review_action(user_id, action_id, expected_updated_at), name=f"action.review.{action_id}"
    )
    _REVIEW_TASKS[key] = task
    TASK_SUPERVISOR.register(task, owner="action_library.review", user_id=user_id)
    task.add_done_callback(
        lambda finished: _REVIEW_TASKS.pop(key, None) if _REVIEW_TASKS.get(key) is finished else None,
    )


async def cancel_action_reviews(user_id: int, action_id: int) -> None:
    tasks = [task for key, task in _REVIEW_TASKS.items() if key[:2] == (user_id, action_id)]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def review_action(user_id: int, action_id: int, expected_updated_at: datetime) -> None:
    revision = expected_updated_at
    try:
        async with _REVIEW_LOCKS.setdefault(user_id, asyncio.Lock()):
            async with SESSION_LOCAL() as db:
                claimed = await begin_review(db, user_id, action_id, expected_updated_at=revision)
                if claimed is None:
                    return
                current, source_json = claimed
                revision = current.updated_at
                await db.commit()
            verdict = await _review_action(user_id, current, ActionReviewSource.model_validate(source_json))
            status: Literal["approved", "deferred", "rejected"] = {
                "approve": "approved",
                "defer": "deferred",
                "reject": "rejected",
            }[verdict.decision]
            async with SESSION_LOCAL() as db:
                await record_review(
                    db, user_id, action_id, status=status, reason=verdict.reason, expected_updated_at=revision
                )
                await db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        reason = (
            "评审已中断，请手动重试"
            if isinstance(exc, asyncio.CancelledError)
            else "评审未完成，请检查服务或身份资料后重试"
        )
        async with SESSION_LOCAL() as db:
            settled_status = await record_review(
                db, user_id, action_id, status="failed", reason=reason, expected_updated_at=revision
            )
            if settled_status == "pending" and ensure_utc(revision) != ensure_utc(expected_updated_at):
                # 认领提交被取消并回滚时，仍须结算受理时的待评审修订。
                await record_review(
                    db, user_id, action_id, status="failed", reason=reason, expected_updated_at=expected_updated_at
                )
            await db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.exception(
            "action review failed", extra={"user_id": user_id, "action_id": action_id, "review_revision": str(revision)}
        )


async def _review_action(user_id: int, current: ActionAssetView, source: ActionReviewSource) -> ActionReviewVerdict:
    async def before_call() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            latest = await action_detail(db, user_id, current.id)
            if (
                latest is None
                or latest.status != "reviewing"
                or ensure_utc(latest.updated_at) != ensure_utc(current.updated_at)
            ):
                raise LlmCallBlockedError("动作评审状态已变化")

    references: list[str] = []
    for path in (source.identity_path, source.portrait_path):
        loaded = await asyncio.to_thread(read_portrait_bytes, path)
        if loaded is None:
            raise ValueError("动作评审缺少可读的冻结身份参考图")
        references.append(await asyncio.to_thread(build_data_uri, loaded[0], loaded[1]))
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
            include={
                "name",
                "motion_description",
                "use_when",
                "avoid_when",
                "clip_kind",
                "duration_seconds",
                "semantic_tags",
                "performance_role",
                "motion_intensity",
                "interruptible",
            }
        ),
        "reason": current.reason,
        "identity": source.identity.features.model_dump(),
        "persona": source.persona,
    }
    return await call_llm_json(
        config,
        ACTION_DEFINITION_REVIEW,
        payload,
        schema=ActionReviewVerdict.model_json_schema(),
        schema_name="action_asset_review",
        parse_output=ActionReviewVerdict.model_validate,
        repair_prompt=ACTION_DEFINITION_REVIEW_REPAIR,
        reference_images=tuple(references),
        preserve_choices=("decision",),
        before_call=before_call,
    )
