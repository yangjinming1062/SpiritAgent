"""共享定义与版本状态的只读投影；目标素材仍从所属模块读取。"""

import json
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .identity import definition_applies_to_source, definition_matches_source
from .models import ActionDefinition, ActionSyncTarget
from .ports import read_variants
from .schemas import (
    ActionDefinitionSnapshot,
    ActionSourceSnapshot,
    ActionTargetSnapshot,
    ActionVariantSummary,
)


def definition_view(row: ActionDefinition, source: ActionSourceSnapshot | None = None) -> ActionDefinitionSnapshot:
    if source is None:
        source = ActionSourceSnapshot.model_validate_json(row.review_source_json)
    return ActionDefinitionSnapshot(
        id=row.id,
        user_id=row.user_id,
        outfit_id=row.outfit_id,
        source_visual_key=row.source_visual_key,
        scope=source.scope,
        origin_target_id=row.origin_target_id,
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
    )


async def definition_snapshot(db: AsyncSession, user_id: int, definition_id: int) -> ActionDefinitionSnapshot | None:
    row = await db.get(ActionDefinition, definition_id)
    return definition_view(row) if row is not None and row.user_id == user_id else None


async def definition_with_source(
    db: AsyncSession,
    user_id: int,
    definition_id: int,
) -> tuple[ActionDefinitionSnapshot, ActionSourceSnapshot] | None:
    row = await db.get(ActionDefinition, definition_id)
    if row is None or row.user_id != user_id:
        return None
    source = ActionSourceSnapshot.model_validate_json(row.review_source_json)
    return definition_view(row, source), source


async def definitions_by_id(
    db: AsyncSession,
    user_id: int,
    definition_ids: Collection[int],
) -> dict[int, ActionDefinitionSnapshot]:
    if not definition_ids:
        return {}
    rows = await db.scalars(
        select(ActionDefinition).where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.id.in_(definition_ids),
        ),
    )
    return {row.id: definition_view(row) for row in rows}


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


async def matching_definitions(
    db: AsyncSession,
    source: ActionSourceSnapshot,
    *,
    match_visual: bool = True,
) -> list[ActionDefinitionSnapshot]:
    definitions = await definitions_for_source(
        db,
        source.user_id,
        source.outfit_id,
        source.source_visual_key if match_visual else None,
    )
    matches = definition_matches_source if match_visual else definition_applies_to_source
    return [definition for definition in definitions if matches(definition, source)]


async def targets_for_definitions(
    db: AsyncSession,
    user_id: int,
    definition_ids: Collection[int],
    *,
    target_id: int | None = None,
    refresh: bool = False,
) -> list[ActionTargetSnapshot]:
    """refresh 覆盖已加载行；同事务写入者须先 flush，再请求数据库当前值。"""
    if not definition_ids:
        return []
    statement = select(ActionSyncTarget).where(
        ActionSyncTarget.user_id == user_id,
        ActionSyncTarget.definition_id.in_(definition_ids),
    )
    if target_id is not None:
        statement = statement.where(ActionSyncTarget.target_id == target_id)
    if refresh:
        statement = statement.execution_options(populate_existing=True)
    rows = (await db.scalars(statement.order_by(ActionSyncTarget.target_id))).all()
    return [ActionTargetSnapshot.model_validate(row) for row in rows]


async def target_snapshot(db: AsyncSession, user_id: int, target_id: int) -> ActionTargetSnapshot | None:
    row = await db.get(ActionSyncTarget, target_id)
    return ActionTargetSnapshot.model_validate(row) if row is not None and row.user_id == user_id else None


async def variant_summaries(db: AsyncSession, user_id: int, definition_id: int) -> list[ActionVariantSummary]:
    return (await variant_summaries_by_definition(db, user_id, (definition_id,)))[definition_id]


async def variant_summaries_by_definition(
    db: AsyncSession,
    user_id: int,
    definition_ids: Collection[int],
) -> dict[int, list[ActionVariantSummary]]:
    result: dict[int, list[ActionVariantSummary]] = {definition_id: [] for definition_id in definition_ids}
    targets = await targets_for_definitions(db, user_id, definition_ids)
    summaries = await read_variants(db, targets)
    for target, current in zip(targets, summaries, strict=True):
        if target.state == "excluded":
            current = ActionVariantSummary(
                target_id=target.target_id,
                action_id=None,
                label=current.label,
                status="excluded",
                reason=target.reason,
            )
        elif target.state == "blocked" and current.status == "not_generated":
            current = ActionVariantSummary(
                target_id=target.target_id,
                action_id=target.action_id,
                label=current.label,
                status="blocked",
                reason=target.reason,
                can_generate=current.can_generate,
            )
        result[target.definition_id].append(current)
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
