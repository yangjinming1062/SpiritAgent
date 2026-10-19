"""播放事实：回执终态与延迟表达意图兑现。"""

from datetime import UTC, datetime, timedelta

from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.actions.materials import accepted_action_asset
from features.actions.models import ActionPlayback, CompanionAction, CompanionActionPack
from features.actions.policy import PLAY_INTENT_TTL_SECONDS
from features.actions.schemas import ActionPlayCommand


def emit_play_command(db: AsyncSession, entry: ActionPlayback, action: CompanionAction) -> None:
    """播放指令与账本同事务写 outbox；客户端按 play_id 回执，queued 不等于 completed。"""
    material = accepted_action_asset(action)
    if material is None:
        raise ValueError("动作没有已采纳素材")
    emit_ws_event(
        db,
        user_id=entry.user_id,
        event_type="companion.action.play_requested",
        payload=ActionPlayCommand(
            play_id=entry.play_id,
            pack_id=entry.pack_id,
            appearance_epoch=entry.appearance_epoch,
            action_id=entry.action_id,
            asset_revision_id=material.metadata_revision,
            expires_at=entry.expires_at.isoformat() if entry.expires_at else None,
            source=entry.source,
        ).model_dump(),
    )


async def record_play_result(
    db: AsyncSession,
    entry: ActionPlayback,
    *,
    status: str,
    visible_duration_ms: int,
    error: str | None,
) -> None:
    """幂等更新播放终态。同状态不重复计；终态不再推进。"""
    if entry.status == status or entry.status in ("completed", "interrupted", "rejected"):
        return
    entry.status = status
    entry.visible_duration_ms = visible_duration_ms
    entry.error = error
    await db.flush()


async def fulfill_deferred_play_intents(db: AsyncSession, action_id: int) -> list[str]:
    """动作就绪后兑现未过期的表达意图；返回已补发的 play_id。过期意图只保留账本不补播，已终态回执不重复指令；所属包已不是当前激活或激活代次已推进（换装后再穿回同一包）的意图记为 rejected。"""
    action = await db.get(CompanionAction, action_id)
    if action is None or accepted_action_asset(action) is None or not action.enabled:
        return []
    pack = await db.get(CompanionActionPack, action.pack_id)
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(ActionPlayback).where(
                    ActionPlayback.action_id == action_id,
                    ActionPlayback.status == "queued",
                ),
            )
        )
        .scalars()
        .all()
    )
    fulfilled: list[str] = []
    for entry in rows:
        if entry.expires_at is not None and entry.expires_at < now:
            continue
        if pack is None or not pack.active or entry.appearance_epoch != pack.appearance_epoch:
            entry.status = "rejected"
            entry.error = "形象已切换，播放请求已取消"
            continue
        entry.expires_at = now + timedelta(seconds=PLAY_INTENT_TTL_SECONDS)
        emit_play_command(db, entry, action)
        fulfilled.append(entry.play_id)
    await db.flush()
    return fulfilled
