"""播放请求与回执协调。queued 不等于 completed。

素材未就绪时保存带 TTL 的表达意图，就绪且未过期才补播。
"""

import uuid
from datetime import UTC, datetime, timedelta

from modules.companion.schemas_actions import ActionPlayCommand, ActionPlayRequest, ActionPlayResult
from modules.ws import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.actions.policy import (
    DEFERRED_PLAY_INTENT_TTL_SECONDS,
    PLAY_INTENT_TTL_SECONDS,
    ActionPolicyError,
    check_can_play,
)
from services.domains.actions.repository import get_action, get_active_pack, record_playback


async def request_playback(
    db: AsyncSession,
    user_id: int,
    request: ActionPlayRequest,
    *,
    source: str = "chat_expression",
    target_device: str = "",
    target_surface: str = "",
    appearance_epoch: int = 0,
    repeat_count: int = 1,
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

    # 制作中：保存表达意图，完成后按有效期决定是否补播。
    if action.status != "succeeded" or not action.video_path:
        if action.status in ("queued", "processing", "running", "result_unknown"):
            play_id = uuid.uuid4().hex
            expires_at = datetime.now(UTC) + timedelta(seconds=DEFERRED_PLAY_INTENT_TTL_SECONDS)
            await record_playback(
                db,
                user_id=user_id,
                play_id=play_id,
                pack_id=pack.id,
                action_id=action.id,
                appearance_epoch=appearance_epoch,
                source=source,
                expires_at=expires_at,
                repeat_count=repeat_count,
            )
            return ActionPlayResult(
                outcome="queued",
                play_id=play_id,
                message="动作制作完成后将视情况补播",
            )
        return ActionPlayResult(outcome="rejected", message="该动作素材尚未就绪")

    try:
        await check_can_play(db, user_id, action, source=source)
    except ActionPolicyError as exc:
        return ActionPlayResult(outcome="rejected", message=str(exc))

    play_id = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(seconds=PLAY_INTENT_TTL_SECONDS)

    await record_playback(
        db,
        user_id=user_id,
        play_id=play_id,
        pack_id=pack.id,
        action_id=action.id,
        appearance_epoch=appearance_epoch,
        source=source,
        expires_at=expires_at,
        repeat_count=repeat_count,
    )

    # 播放指令与账本同事务提交；客户端按 play_id 回执，queued 不等于 completed。
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.action.play_requested",
        payload=ActionPlayCommand(
            play_id=play_id,
            target_device=target_device,
            target_surface=target_surface,
            pack_id=pack.id,
            appearance_epoch=appearance_epoch,
            action_id=action.id,
            asset_revision_id=action.metadata_revision,
            repeat_count=repeat_count,
            expires_at=expires_at.isoformat(),
            source=source,
        ).model_dump(),
    )

    return ActionPlayResult(outcome="queued", play_id=play_id, message="播放指令已排队")
