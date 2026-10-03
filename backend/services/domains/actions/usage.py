"""播放事实：回执终态与延迟表达意图兑现。"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.companion import ActionPlayback, ActionPlayCommand, CompanionAction, CompanionActionPack
from modules.ws import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .materials import accepted_action_asset
from .policy import PLAY_INTENT_TTL_SECONDS


def action_to_dict(action: CompanionAction) -> dict[str, Any]:
    """动作元信息字典（含时长），供检索与提示词资料块共用。"""
    material = accepted_action_asset(action)
    duration_ms = (
        material.actual_duration_ms
        if material
        else action.actual_duration_ms or int((action.target_duration_seconds or 0) * 1000)
    )
    return {
        "action_id": action.id,
        "key": action.key,
        "name": action.name,
        "system_slot": action.system_slot or "",
        "kind": material.kind if material else action.kind,
        "motion_description": action.motion_description,
        "use_when": json.loads(action.use_when or "[]"),
        "avoid_when": json.loads(action.avoid_when or "[]"),
        "duration_ms": duration_ms,
        "duration_seconds": round(duration_ms / 1000, 3) if duration_ms else 0.0,
        "loopable": material.loopable if material else action.loopable,
        "enabled": action.enabled,
    }


# 面向模型的动作条目字段：陪伴快照与闲置表达共用，不含内部标识与状态。
ACTION_PROMPT_KEYS: tuple[str, ...] = (
    "action_id",
    "name",
    "motion_description",
    "use_when",
    "avoid_when",
    "kind",
    "duration_seconds",
)


def action_prompt_entry(action: CompanionAction) -> dict[str, Any]:
    """面向模型的动作条目：取 `action_to_dict` 中的 `ACTION_PROMPT_KEYS` 字段。"""
    item = action_to_dict(action)
    return {key: item[key] for key in ACTION_PROMPT_KEYS}


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
