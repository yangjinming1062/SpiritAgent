"""播放事实：回执终态与延迟表达意图兑现。"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.companion import ActionPlayback, ActionPlayCommand, CompanionAction
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .policy import PLAY_INTENT_TTL_SECONDS


def action_to_dict(action: CompanionAction) -> dict[str, Any]:
    """动作元信息字典（无使用统计与冷却）。含时长，供检索与提示词资料块共用。"""
    duration_ms = action.actual_duration_ms or int((action.target_duration_seconds or 0) * 1000)
    return {
        "action_id": action.id,
        "key": action.key,
        "name": action.name,
        "system_slot": action.system_slot or "",
        "kind": action.kind,
        "motion_description": action.motion_description,
        "use_when": json.loads(action.use_when or "[]"),
        "avoid_when": json.loads(action.avoid_when or "[]"),
        "tags": json.loads(action.tags or "[]"),
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 3) if duration_ms else 0.0,
        "loopable": action.loopable,
        "enabled": action.enabled,
    }


async def record_play_result(
    db: AsyncSession,
    play_id: str,
    *,
    status: str,
    visible_duration_ms: int = 0,
    error: str | None = None,
) -> ActionPlayback | None:
    """按 play_id 幂等更新播放终态。同状态不重复计；终态不再推进。"""
    row = await db.execute(select(ActionPlayback).where(ActionPlayback.play_id == play_id))
    entry = row.scalar_one_or_none()
    if entry is None:
        return None
    if entry.status == status or entry.status in ("completed", "interrupted", "rejected"):
        return entry

    entry.status = status
    entry.visible_duration_ms = visible_duration_ms
    entry.error = error
    await db.flush()
    return entry


async def fulfill_deferred_play_intents(db: AsyncSession, action_id: int) -> list[str]:
    """动作就绪后兑现未过期的表达意图；返回已补发的 play_id。

    过期意图只保留账本，不补播；已终态的回执不重复指令。"""
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
        if entry.expires_at is not None:
            expires = entry.expires_at if entry.expires_at.tzinfo else entry.expires_at.replace(tzinfo=UTC)
            if expires < now:
                continue
        action = await db.get(CompanionAction, action_id)
        if action is None or action.status != "succeeded" or not action.video_path or not action.enabled:
            continue
        expires_at = now + timedelta(seconds=PLAY_INTENT_TTL_SECONDS)
        entry.expires_at = expires_at
        emit_ws_event(
            db,
            user_id=entry.user_id,
            event_type="companion.action.play_requested",
            payload=ActionPlayCommand(
                play_id=entry.play_id,
                target_device=entry.target_device,
                target_surface=entry.target_surface,
                pack_id=entry.pack_id,
                appearance_epoch=entry.appearance_epoch,
                action_id=entry.action_id,
                asset_revision_id=action.metadata_revision,
                repeat_count=entry.repeat_count,
                expires_at=expires_at.isoformat(),
                source=entry.source,
            ).model_dump(),
        )
        fulfilled.append(entry.play_id)
    await db.flush()
    return fulfilled
