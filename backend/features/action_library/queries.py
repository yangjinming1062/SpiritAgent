"""共享定义与版本状态的只读投影；目标素材仍从所属模块读取。"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ActionDefinition, ActionSyncTarget
from .ports import read_variant
from .schemas import ActionDefinitionSnapshot, ActionSourceSnapshot, ActionTargetSnapshot, ActionVariantSummary


def definition_view(row: ActionDefinition) -> ActionDefinitionSnapshot:
    return ActionDefinitionSnapshot(
        id=row.id,
        user_id=row.user_id,
        outfit_id=row.outfit_id,
        source_visual_key=row.source_visual_key,
        name=row.name,
        motion_description=row.motion_description,
        use_when=json.loads(row.use_when_json),
        avoid_when=json.loads(row.avoid_when_json),
        clip_kind=row.clip_kind,
        duration_seconds=row.duration_seconds,
        reason=row.reason,
        source=row.source,
        status=row.status,
        review_reason=row.review_reason,
        reuse_definition_id=row.reuse_definition_id,
        desktop_continuity=row.desktop_continuity,
        desktop_allowed_triggers=json.loads(row.desktop_allowed_triggers_json),
    )


async def definition_snapshot(db: AsyncSession, user_id: int, definition_id: int) -> ActionDefinitionSnapshot | None:
    row = await db.get(ActionDefinition, definition_id)
    return definition_view(row) if row is not None and row.user_id == user_id else None


async def definition_review_source(db: AsyncSession, user_id: int, definition_id: int) -> ActionSourceSnapshot | None:
    row = await db.get(ActionDefinition, definition_id)
    return (
        ActionSourceSnapshot.model_validate_json(row.review_source_json)
        if row is not None and row.user_id == user_id
        else None
    )


async def definitions_for_source(
    db: AsyncSession,
    user_id: int,
    outfit_id: int | None,
    source_visual_key: str | None,
) -> list[ActionDefinitionSnapshot]:
    statement = select(ActionDefinition).where(
        ActionDefinition.user_id == user_id,
        ActionDefinition.outfit_id == outfit_id,
    )
    if source_visual_key is not None:
        statement = statement.where(ActionDefinition.source_visual_key == source_visual_key)
    rows = (await db.scalars(statement.order_by(ActionDefinition.updated_at.desc(), ActionDefinition.id.desc()))).all()
    return [definition_view(row) for row in rows]


async def targets_for_definition(db: AsyncSession, user_id: int, definition_id: int) -> list[ActionTargetSnapshot]:
    rows = (
        await db.scalars(
            select(ActionSyncTarget)
            .where(
                ActionSyncTarget.user_id == user_id,
                ActionSyncTarget.definition_id == definition_id,
            )
            .order_by(ActionSyncTarget.target_kind, ActionSyncTarget.target_id),
        )
    ).all()
    return [ActionTargetSnapshot.model_validate(row) for row in rows]


async def target_snapshot(db: AsyncSession, user_id: int, target_id: int) -> ActionTargetSnapshot | None:
    row = await db.get(ActionSyncTarget, target_id)
    return ActionTargetSnapshot.model_validate(row) if row is not None and row.user_id == user_id else None


async def variant_summaries(db: AsyncSession, user_id: int, definition_id: int) -> list[ActionVariantSummary]:
    result: list[ActionVariantSummary] = []
    for target in await targets_for_definition(db, user_id, definition_id):
        current = await read_variant(db, target)
        if target.state == "excluded":
            current = ActionVariantSummary(
                target_kind=target.target_kind,
                target_id=target.target_id,
                action_id=None,
                label=current.label if current else "",
                status="excluded",
                reason=target.reason,
            )
        elif target.state == "blocked" and (current is None or current.status == "not_generated"):
            current = ActionVariantSummary(
                target_kind=target.target_kind,
                target_id=target.target_id,
                action_id=target.action_id,
                label=current.label if current else "",
                status="blocked",
                reason=target.reason,
                can_generate=current.can_generate if current else False,
            )
        if current is not None:
            result.append(current)
    return result


async def recoverable_work(
    db: AsyncSession,
    user_id: int | None = None,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    definitions = select(ActionDefinition.user_id, ActionDefinition.id).where(
        (ActionDefinition.status == "pending") | ActionDefinition.fanout_pending,
    )
    targets = select(ActionSyncTarget.user_id, ActionSyncTarget.id).where(ActionSyncTarget.state == "pending")
    if user_id is not None:
        definitions = definitions.where(ActionDefinition.user_id == user_id)
        targets = targets.where(ActionSyncTarget.user_id == user_id)
    return ([tuple(row) for row in await db.execute(definitions)], [tuple(row) for row in await db.execute(targets)])
