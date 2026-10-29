"""动作素材的受理、编辑与评审状态维护。

登记定义本身不启动图片或视频制作；名称不承担跨来源唯一身份。
"""

from datetime import datetime
from typing import Any, Literal

from infrastructure.config import SETTINGS
from infrastructure.domain_locks import lock_domain
from infrastructure.text import ensure_utc
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.life.schemas import LifeStepRequest
from features.life.use_cases import (
    bind_step_in_session,
    require_step_submission,
    step_for_request,
    update_business_steps_in_session,
)

from .models import ActionDefinition
from .presets import ACTION_PRESETS
from .schemas import ActionAssetDesign, ActionAssetSource, ActionAssetStatus, ActionAssetView
from .views import definition_view, normalize_action_name


async def create_action(
    db: AsyncSession,
    user_id: int,
    design: ActionAssetDesign,
    *,
    source: ActionAssetSource,
    review_source_json: dict,
    life_step: LifeStepRequest | None = None,
) -> ActionAssetView:
    """登记一份新的动作素材；评审由 review 模块异步执行。"""

    await lock_domain(db, user_id, "actions")
    if life_step is not None:
        original = await step_for_request(db, user_id, life_step.idempotency_key)
        if original is not None and original.business_ref.get("action_id"):
            row = await db.get(ActionDefinition, original.business_ref["action_id"])
            if row is None or row.user_id != user_id:
                raise ValueError("原动作已删除，不能重新执行同一请求")
            return definition_view(row)
    await require_step_submission(db, user_id, life_step)
    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        raise ValueError("伙伴自主新增动作已关闭")
    existing = await db.scalars(
        select(ActionDefinition).where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.source != "preset",
            ActionDefinition.normalized_name == normalize_action_name(design.name),
        ),
    )
    for item in existing:
        if (
            item.motion_description == design.motion_description
            and item.clip_kind == design.clip_kind
            and item.duration_seconds == design.duration_seconds
            and item.use_when_json == design.use_when
            and item.avoid_when_json == design.avoid_when
            and item.allowed_triggers_json == design.triggers
            and item.semantic_tags_json == design.semantic_tags
            and item.performance_role == design.performance_role
            and item.motion_intensity == design.motion_intensity
            and item.interruptible == design.interruptible
        ):
            if life_step is not None:
                await bind_step_in_session(db, user_id, life_step, business_ref={"action_id": item.id})
                await settle_action_steps(db, item)
            return definition_view(item)
    row = ActionDefinition(
        user_id=user_id,
        name=design.name,
        normalized_name=normalize_action_name(design.name),
        motion_description=design.motion_description,
        use_when_json=list(design.use_when),
        avoid_when_json=list(design.avoid_when),
        allowed_triggers_json=list(design.triggers),
        clip_kind=design.clip_kind,
        duration_seconds=design.duration_seconds,
        semantic_tags_json=list(design.semantic_tags),
        performance_role=design.performance_role,
        motion_intensity=design.motion_intensity,
        interruptible=design.interruptible,
        reason=design.reason,
        source=source,
        status="pending",
        review_reason="",
        review_source_json=review_source_json,
    )
    db.add(row)
    await db.flush()
    if life_step is not None:
        await bind_step_in_session(db, user_id, life_step, business_ref={"action_id": row.id})
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row)


async def ensure_builtin_actions(db: AsyncSession, user_id: int) -> None:
    """按稳定键补齐通用定义；不读取个人身份，也不启动评审或媒体制作。"""
    await lock_domain(db, user_id, "actions")
    keys = set(await db.scalars(select(ActionDefinition.preset_key).where(ActionDefinition.user_id == user_id)))
    for preset in ACTION_PRESETS:
        if preset.key in keys:
            continue
        db.add(
            ActionDefinition(
                user_id=user_id,
                preset_key=preset.key,
                source="preset",
                status="approved",
                name=preset.name,
                normalized_name=normalize_action_name(preset.name),
                motion_description=preset.motion_description,
                use_when_json=preset.use_when,
                avoid_when_json=preset.avoid_when,
                allowed_triggers_json=preset.triggers,
                clip_kind=preset.clip_kind,
                duration_seconds=preset.duration_seconds,
                semantic_tags_json=list(preset.semantic_tags),
                performance_role=preset.performance_role,
                motion_intensity=preset.motion_intensity,
                interruptible=preset.interruptible,
                reason="",
                review_reason="",
                review_source_json={},
            )
        )
    await db.flush()


async def _locked_action(db: AsyncSession, user_id: int, action_id: int) -> ActionDefinition | None:
    await lock_domain(db, user_id, "actions")
    return await db.scalar(
        select(ActionDefinition)
        .where(ActionDefinition.user_id == user_id, ActionDefinition.id == action_id)
        .execution_options(populate_existing=True),
    )


def require_action_editable(current: ActionAssetView, expected_updated_at: datetime) -> None:
    if current.source == "preset":
        raise ValueError("内置动作不可修改或删除")
    if ensure_utc(current.updated_at) != ensure_utc(expected_updated_at):
        raise ValueError("动作已更新，请保留修改并刷新核对后重试")
    if current.status in {"pending", "reviewing"}:
        raise ValueError("动作正在等待评审或评审中，请等待结果")


def prepare_action_update(
    current: ActionAssetView,
    design: ActionAssetDesign,
    expected_updated_at: datetime,
) -> ActionAssetDesign | None:
    """装配前判断是否需要身份参考，写入时在锁内重核同一规则。"""
    require_action_editable(current, expected_updated_at)
    fields = set(ActionAssetDesign.model_fields)
    previous = current.model_dump(include=fields)
    submitted = design.model_dump(include=design.model_fields_set & fields)
    if (
        "clip_kind" in submitted
        and submitted["clip_kind"] != previous["clip_kind"]
        and "performance_role" not in submitted
    ):
        submitted["performance_role"] = None
    updated = ActionAssetDesign.model_validate(previous | submitted)
    updated.allowed_triggers = updated.triggers
    return updated if previous != updated.model_dump() else None


def require_review_retry(current: ActionAssetView, expected_updated_at: datetime) -> None:
    require_action_editable(current, expected_updated_at)
    if current.status not in {"failed", "deferred"}:
        raise ValueError("只有评审失败或待补充资料的动作可以重试；未通过请修改后提交")


def _reset_review(row: ActionDefinition, source: dict[str, Any]) -> None:
    row.status, row.review_reason, row.review_source_json = "pending", "", source


async def update_action(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    design: ActionAssetDesign,
    *,
    expected_updated_at: datetime,
    review_source_json: dict,
) -> ActionAssetView | None:
    """编辑动作语义；内容变化后回到待评审，不追溯已生成影像。"""

    row = await _locked_action(db, user_id, action_id)
    if row is None:
        return None
    current = definition_view(row)
    updated = prepare_action_update(current, design, expected_updated_at)
    if updated is None:
        return current
    design = updated
    row.name = design.name
    row.normalized_name = normalize_action_name(design.name)
    row.motion_description = design.motion_description
    row.use_when_json = list(design.use_when)
    row.avoid_when_json = list(design.avoid_when)
    row.allowed_triggers_json = list(design.triggers)
    row.clip_kind = design.clip_kind
    row.duration_seconds = design.duration_seconds
    row.semantic_tags_json = list(design.semantic_tags)
    row.performance_role = design.performance_role
    row.motion_intensity = design.motion_intensity
    row.interruptible = design.interruptible
    row.reason = design.reason
    _reset_review(row, review_source_json)
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row)


async def delete_action(db: AsyncSession, user_id: int, action_id: int) -> bool:
    row = await _locked_action(db, user_id, action_id)
    if row is None:
        return False
    if row.source == "preset":
        raise ValueError("内置动作不可删除")
    await update_business_steps_in_session(
        db,
        user_id,
        kind="action.design",
        business_ref={"action_id": action_id},
        state="failed",
        error="动作已删除",
    )
    await db.delete(row)
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return True


async def retry_review(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    expected_updated_at: datetime,
    review_source_json: dict[str, Any],
) -> ActionAssetView | None:
    row = await _locked_action(db, user_id, action_id)
    if row is None:
        return None
    require_review_retry(definition_view(row), expected_updated_at)
    _reset_review(row, review_source_json)
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row)


async def begin_review(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    expected_updated_at: datetime,
) -> tuple[ActionAssetView, dict[str, Any]] | None:
    row = await _locked_action(db, user_id, action_id)
    if row is None or row.status != "pending" or ensure_utc(row.updated_at) != ensure_utc(expected_updated_at):
        return None
    row.status = "reviewing"
    await db.flush()
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return definition_view(row), row.review_source_json


async def record_review(
    db: AsyncSession,
    user_id: int,
    action_id: int,
    *,
    status: Literal["approved", "deferred", "rejected", "failed"],
    reason: str,
    expected_updated_at: datetime,
) -> ActionAssetStatus | None:
    """只有本次修订可结算；排队期间取消也能保存技术失败。"""

    row = await _locked_action(db, user_id, action_id)
    if row is None:
        return None
    if ensure_utc(row.updated_at) != ensure_utc(expected_updated_at) or row.status not in (
        {"pending", "reviewing"} if status == "failed" else {"reviewing"}
    ):
        return row.status
    row.status = status
    row.review_reason = reason
    await db.flush()
    await settle_action_steps(db, row)
    emit_ws_event(db, user_id=user_id, event_type="companion.action.updated", payload={})
    return status


async def settle_action_steps(db: AsyncSession, row: ActionDefinition) -> None:
    if row.status not in {"approved", "rejected", "deferred", "failed"}:
        return
    await update_business_steps_in_session(
        db,
        row.user_id,
        kind="action.design",
        business_ref={"action_id": row.id},
        state="completed" if row.status == "approved" else "failed",
        result_ref={"action_id": row.id} if row.status == "approved" else {},
        error=row.review_reason if row.status != "approved" else None,
    )


async def recover_interrupted_reviews(db: AsyncSession, user_id: int | None = None) -> int:
    """启动和账户恢复时终结遗留任务，不自动重新付费。"""

    statement = select(ActionDefinition).where(ActionDefinition.status.in_(("pending", "reviewing")))
    if user_id is not None:
        statement = statement.where(ActionDefinition.user_id == user_id)
    result = await db.execute(statement)
    count = 0
    for row in result.scalars():
        row.status = "failed"
        row.review_reason = "评审中断，请手动重试"
        await settle_action_steps(db, row)
        emit_ws_event(db, user_id=row.user_id, event_type="companion.action.updated", payload={})
        count += 1
    if count:
        await db.flush()
    return count
