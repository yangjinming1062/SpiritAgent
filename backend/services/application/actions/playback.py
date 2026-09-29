"""播放请求与回执协调。queued 不等于 completed。

素材未就绪时保存带 TTL 与外观代次的表达意图；就绪时未过期且代次仍是当前激活才补播。
"""

import uuid
from datetime import UTC, datetime, timedelta

from modules.companion import ActionPlayRequest, ActionPlayResult
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions import (
    DEFERRED_PLAY_INTENT_TTL_SECONDS,
    PLAY_INTENT_TTL_SECONDS,
    emit_play_command,
    get_action,
    get_active_pack,
    record_playback,
)


async def request_playback(
    db: AsyncSession,
    user_id: int,
    request: ActionPlayRequest,
    *,
    source: str,
) -> ActionPlayResult:
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return ActionPlayResult(outcome="rejected", message="当前没有可用的形象动作")

    if request.expected_pack_id is not None and request.expected_pack_id != pack.id:
        return ActionPlayResult(outcome="rejected", message="形象已切换，播放请求已取消")

    action = await get_action(db, request.action_id)
    if action is None or action.pack_id != pack.id:
        return ActionPlayResult(outcome="rejected", message="动作不存在或不属于当前形象")

    if not action.enabled:
        return ActionPlayResult(outcome="rejected", message="该动作已停用")

    ready = action.status == "succeeded" and bool(action.video_path)
    # 制作中：保存表达意图，完成后按有效期与外观代次决定是否补播。
    if not ready and action.status not in ("queued", "processing", "result_unknown"):
        return ActionPlayResult(outcome="rejected", message="该动作素材尚未就绪")
    ttl = PLAY_INTENT_TTL_SECONDS if ready else DEFERRED_PLAY_INTENT_TTL_SECONDS
    entry = await record_playback(
        db,
        user_id=user_id,
        play_id=uuid.uuid4().hex,
        pack_id=pack.id,
        action_id=action.id,
        appearance_epoch=pack.appearance_epoch,
        source=source,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
    )
    if not ready:
        return ActionPlayResult(outcome="queued", play_id=entry.play_id, message="动作制作完成后将视情况补播")
    emit_play_command(db, entry, action)
    return ActionPlayResult(outcome="queued", play_id=entry.play_id, message="播放指令已排队")
