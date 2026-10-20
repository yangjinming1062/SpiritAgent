"""共享创意受理和目标登记；不直接操作窗口或桌景的 ORM。"""

import json
import unicodedata
from datetime import timedelta

from infrastructure.config import SETTINGS
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row

from .identity import definition_applies_to_source, definition_matches_source, same_review_reference
from .models import ActionDefinition, ActionSyncTarget
from .ports import read_source
from .queries import definition_view
from .schemas import (
    ActionDefinitionDesign,
    ActionDefinitionSnapshot,
    ActionSourceSnapshot,
    ActionTargetKind,
    ActionTargetSnapshot,
    DefinitionAcceptance,
)


def normalize_action_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


async def accept_definition(
    db: AsyncSession,
    user_id: int,
    design: ActionDefinitionDesign,
    *,
    source: str,
    origin_kind: ActionTargetKind,
    expected_target_id: int | None = None,
) -> DefinitionAcceptance:
    if source not in {"autonomous", "user_requested"}:
        raise ValueError("未知的动作设计来源")
    if source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        return DefinitionAcceptance(definition_id=0, status="rejected", message="自主创建新动作当前已关闭")
    await lock_user_row(db, user_id)
    target = await read_source(db, user_id, origin_kind, expected_target_id)
    if (
        target is None
        or not target.ready
        or (target.scope == "outfit" and target.outfit_id is None)
        or not target.source_visual_key
    ):
        return DefinitionAcceptance(
            definition_id=0,
            status="rejected",
            message=(target.blocked_reason if target else None) or "当前形象缺少可用的冻结参考",
        )
    normalized = normalize_action_name(design.name)
    row = await db.scalar(
        select(ActionDefinition).where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.outfit_id == target.outfit_id,
            ActionDefinition.source_visual_key == target.source_visual_key,
            ActionDefinition.normalized_name == normalized,
        ),
    )
    if row is not None and not definition_matches_source(definition_view(row), target):
        raise ValueError("动作定义与当前制作来源不一致")
    if row is None:
        row = ActionDefinition(
            user_id=user_id,
            outfit_id=target.outfit_id,
            source_visual_key=target.source_visual_key,
            normalized_name=normalized,
            name=design.name,
            motion_description=design.motion_description,
            use_when_json=json.dumps(design.use_when, ensure_ascii=False),
            avoid_when_json=json.dumps(design.avoid_when, ensure_ascii=False),
            clip_kind=design.clip_kind,
            duration_seconds=design.duration_seconds,
            reason=design.reason,
            source=source,
            status="pending",
            review_source_json=target.model_dump_json(),
            origin_kind=origin_kind,
            origin_target_id=target.target_id,
            fanout_pending=False,
        )
        db.add(row)
        await db.flush()
    canonical_id = row.reuse_definition_id or row.id
    local = await db.scalar(
        select(ActionSyncTarget).where(
            ActionSyncTarget.user_id == user_id,
            ActionSyncTarget.definition_id == canonical_id,
            ActionSyncTarget.target_kind == origin_kind,
            ActionSyncTarget.target_id == target.target_id,
        ),
    )
    if local is not None and local.state == "excluded":
        return DefinitionAcceptance(
            definition_id=row.id,
            status="rejected",
            message="这个动作已在当前画面移除，请使用新的名称重新设计",
        )
    if row.status == "deferred" or (
        row.status == "rejected" and ensure_utc(row.updated_at) < utc_now() - timedelta(days=7)
    ):
        # 同名始终沿用冻结设计；重提只重新评审，不改写已登记的动作含义。
        row.status, row.source, row.review_reason = "pending", source, ""
        row.review_source_json = target.model_dump_json()
        row.origin_kind, row.origin_target_id = origin_kind, target.target_id
    return DefinitionAcceptance(
        definition_id=row.id,
        status=row.status,
        action_id=local.action_id if local else None,
        message=row.review_reason
        or (
            ("动作已登记，将在当前桌景制作" if target.scope == "target" else "动作已登记，各画面版本分别制作")
            if row.status in {"approved", "reused"}
            else "提案已受理，将独立评审后制作"
        ),
    )


async def ensure_target(
    db: AsyncSession,
    user_id: int,
    definition_id: int,
    target: ActionSourceSnapshot,
    *,
    automatic: bool,
) -> ActionTargetSnapshot:
    definition = await db.get(ActionDefinition, definition_id)
    if definition is None or definition.user_id != user_id or target.user_id != user_id:
        raise ValueError("动作或制作目标不属于当前账户")
    if not definition_applies_to_source(definition_view(definition), target):
        raise ValueError("制作目标不属于这个动作的来源范围")
    mismatch = definition.source_visual_key != target.source_visual_key
    row = await db.scalar(
        select(ActionSyncTarget).where(
            ActionSyncTarget.definition_id == definition_id,
            ActionSyncTarget.target_kind == target.target_kind,
            ActionSyncTarget.target_id == target.target_id,
        ),
    )
    if row is None:
        row = ActionSyncTarget(
            user_id=user_id,
            definition_id=definition_id,
            target_kind=target.target_kind,
            target_id=target.target_id,
            automatic=automatic,
            state="pending"
            if automatic and target.ready and not mismatch
            else "manual"
            if target.ready and not mismatch
            else "blocked",
            reason="冻结的制作来源不同" if mismatch else target.blocked_reason,
        )
        db.add(row)
        await db.flush()
    elif row.state != "excluded" and mismatch:
        row.state, row.reason = "blocked", "冻结的制作来源不同"
    elif row.state == "pending" and not target.ready:
        row.state, row.reason = "blocked", target.blocked_reason
    elif row.state == "blocked" and row.automatic and target.ready and not mismatch:
        row.state, row.reason = "pending", None
    return ActionTargetSnapshot.model_validate(row)


async def register_source_targets(db: AsyncSession, source: ActionSourceSnapshot) -> None:
    """目标创建或就绪事务内登记意图，提交后回调丢失仍可按目标恢复。"""
    if (source.scope == "outfit" and source.outfit_id is None) or not source.source_visual_key:
        return
    await lock_user_row(db, source.user_id)
    definitions = await db.scalars(
        select(ActionDefinition.id).where(
            ActionDefinition.user_id == source.user_id,
            ActionDefinition.outfit_id == source.outfit_id,
            ActionDefinition.source_visual_key == source.source_visual_key,
            ActionDefinition.status == "approved",
        ),
    )
    for definition_id in definitions:
        definition = await db.get(ActionDefinition, definition_id)
        if definition is not None and definition_matches_source(definition_view(definition), source):
            await ensure_target(db, source.user_id, definition_id, source, automatic=True)


async def bind_target(db: AsyncSession, user_id: int, target_id: int, action_id: int) -> None:
    row = await db.get(ActionSyncTarget, target_id)
    if row is None or row.user_id != user_id or row.state == "excluded":
        raise ValueError("制作目标已移除")
    if row.action_id is not None and row.action_id != action_id:
        raise ValueError("制作目标已绑定其他动作")
    row.action_id = action_id


async def mark_target_dispatched(db: AsyncSession, user_id: int, target_id: int) -> None:
    row = await db.get(ActionSyncTarget, target_id)
    if row is not None and row.user_id == user_id and row.state == "pending":
        row.state, row.reason = "dispatched", None


async def release_ready_target(db: AsyncSession, user_id: int, target_id: int) -> None:
    row = await db.get(ActionSyncTarget, target_id)
    if row is not None and row.user_id == user_id and row.automatic and row.state == "blocked":
        row.state, row.reason = "pending", None


async def block_target(db: AsyncSession, user_id: int, target_id: int, reason: str) -> None:
    row = await db.get(ActionSyncTarget, target_id)
    if row is not None and row.user_id == user_id and row.state != "excluded":
        row.state, row.reason = "blocked", reason[:400]


async def exclude_target(
    db: AsyncSession,
    user_id: int,
    definition_id: int,
    kind: ActionTargetKind,
    target_id: int,
) -> None:
    row = await db.scalar(
        select(ActionSyncTarget).where(
            ActionSyncTarget.user_id == user_id,
            ActionSyncTarget.definition_id == definition_id,
            ActionSyncTarget.target_kind == kind,
            ActionSyncTarget.target_id == target_id,
        ),
    )
    if row is not None:
        row.state, row.action_id, row.reason = "excluded", None, "此画面版本已移除"


async def remove_container_targets(
    db: AsyncSession,
    user_id: int,
    kind: ActionTargetKind,
    target_id: int,
) -> None:
    """容器整体删除后不再保留无归属目标；单版本删除仍保留排除记录。"""
    await lock_user_row(db, user_id)
    await db.execute(
        delete(ActionSyncTarget).where(
            ActionSyncTarget.user_id == user_id,
            ActionSyncTarget.target_kind == kind,
            ActionSyncTarget.target_id == target_id,
        ),
    )
    local_definitions = await db.scalars(
        select(ActionDefinition).where(
            ActionDefinition.user_id == user_id,
            ActionDefinition.origin_kind == kind,
            ActionDefinition.origin_target_id == target_id,
        ),
    )
    for definition in local_definitions:
        if definition_view(definition).scope == "target":
            await db.delete(definition)


async def record_review(
    db: AsyncSession,
    user_id: int,
    definition_id: int,
    decision: str,
    reason: str,
    reuse_id: int | None = None,
) -> ActionDefinitionSnapshot | None:
    await lock_user_row(db, user_id)
    row = await db.get(ActionDefinition, definition_id, populate_existing=True)
    if row is None or row.user_id != user_id or row.status not in {"pending", "reviewing"}:
        return None
    if row.source == "autonomous" and not SETTINGS.action_autocreate_enabled:
        decision, reason, reuse_id = "defer", "自主创建新动作当前已关闭", None
    review_source = ActionSourceSnapshot.model_validate_json(row.review_source_json)
    if decision in {"approve", "reuse"} and review_source.scope == "target":
        current_source = await read_source(db, user_id, review_source.target_kind, review_source.target_id)
        if (
            current_source is None
            or not current_source.ready
            or not same_review_reference(review_source, current_source)
        ):
            decision, reason, reuse_id = "defer", "桌景的确认画面或着装资料已变化，请重新评审", None
    if decision == "reuse":
        canonical = await db.get(ActionDefinition, reuse_id) if reuse_id is not None else None
        if (
            canonical is None
            or canonical.id == row.id
            or canonical.status != "approved"
            or not definition_matches_source(definition_view(canonical), review_source)
        ):
            raise ValueError("复用动作不属于当前制作来源范围")
    elif reuse_id is not None:
        raise ValueError("只有复用结论可以指定已有动作")
    row.status = {"approve": "approved", "reuse": "reused", "defer": "deferred", "reject": "rejected"}[decision]
    row.review_reason, row.reuse_definition_id = reason[:400], reuse_id
    row.fanout_pending = decision in {"approve", "reuse"}
    await db.flush()
    return definition_view(row)


async def begin_review(db: AsyncSession, user_id: int, definition_id: int) -> bool:
    """模型提交前持久认领；进程丢失回执时不把已计费调用自动重发。"""
    await lock_user_row(db, user_id)
    row = await db.get(ActionDefinition, definition_id, populate_existing=True)
    if row is None or row.user_id != user_id or row.status != "pending":
        return False
    row.status = "reviewing"
    return True


async def recover_interrupted_reviews(db: AsyncSession) -> None:
    rows = await db.scalars(select(ActionDefinition).where(ActionDefinition.status == "reviewing"))
    for row in rows:
        row.status = "deferred"
        row.review_reason = "评审已提交但结果未保存，请手动重试评审"


async def finish_fanout(db: AsyncSession, user_id: int, definition_id: int) -> None:
    row = await db.get(ActionDefinition, definition_id)
    if row is not None and row.user_id == user_id:
        row.fanout_pending = False
