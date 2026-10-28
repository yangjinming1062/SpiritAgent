"""现有画面的保留、静态素材派发与实际显示回执。"""

from datetime import timedelta
from uuid import uuid4

from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.life.use_cases import current_presentation_revision, presentation_revision_matches

from .models import VisualPlayback, VisualPresentation
from .ports import request_base_motion_preparation
from .repository import (
    emit_playback_receipt,
    notify_visuals_updated,
    presentation_outfit,
    require_presentation_row,
    touch_presentation,
)
from .schemas import StageIntent, StagePresentation, StageReceipt, VisualPlayCommand


async def record_stage_intent(db: AsyncSession, user_id: int, intent: StageIntent) -> None:
    """生活侧投影写入舞台意图；呈现系统只读取，不反写生活事实。"""

    state = await require_presentation_row(db, user_id, lock=True)
    if not await presentation_revision_matches(db, user_id, intent.life_revision):
        return
    if state.intent_json and state.intent_json["life_revision"] > intent.life_revision:
        return
    payload = intent.model_dump(mode="json")
    if state.intent_json != payload:
        state.intent_json = payload
        await notify_visuals_updated(db, user_id)


async def _cancel_pending_command(db: AsyncSession, state: VisualPresentation) -> None:
    if not state.pending_command_json:
        return
    command = VisualPlayCommand.model_validate(state.pending_command_json)
    await db.execute(
        update(VisualPlayback)
        .where(
            VisualPlayback.user_id == state.user_id,
            VisualPlayback.play_id == command.play_id,
            VisualPlayback.status.in_(("queued", "claimed")),
        )
        .values(status="expired", error="已按最新活动更新画面", updated_at=utc_now())
    )
    emit_playback_receipt(db, state.user_id, command.play_id, "expired", None)
    state.pending_command_json = None


async def present_static(user_id: int, snapshot: StagePresentation, *, expected_state_revision: int) -> bool:
    async with SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        presence = get_presentation_snapshot(user_id)
        if (
            presence is None
            or state.state_revision != expected_state_revision
            or not get_user_proactive_record(user_id).available
            or presence.revision != snapshot.presentation_epoch
            or not await presentation_revision_matches(db, user_id, snapshot.life_revision)
        ):
            return False
        await touch_presentation(db, user_id)
        state.presentation_epoch = presence.revision
        await _cancel_pending_command(db, state)
        state.pending_static_json = snapshot.model_copy(update={"state_revision": state.state_revision}).model_dump(
            mode="json"
        )
        await db.commit()
        return True


async def restore_static_presentation(
    db: AsyncSession, user_id: int, state: VisualPresentation, *, presentation_epoch: int
) -> bool:
    """重连与模式交接复用冻结素材，以新回执身份恢复；不重新选择或制作内容。"""
    raw = state.pending_static_json or state.current_static_json
    if raw is None:
        return False
    snapshot = StagePresentation.model_validate(raw)
    life_revision = await current_presentation_revision(db, user_id)
    if (
        state.pending_static_json is not None
        and not state.pending_command_json
        and snapshot.presentation_epoch == presentation_epoch
        and snapshot.life_revision == life_revision
        and snapshot.state_revision == state.state_revision
    ):
        return True
    await _cancel_pending_command(db, state)
    await touch_presentation(db, user_id)
    state.presentation_epoch = presentation_epoch
    state.pending_static_json = snapshot.model_copy(
        update={
            "request_id": uuid4().hex,
            "presentation_epoch": presentation_epoch,
            "state_revision": state.state_revision,
            "life_revision": life_revision,
            "receipt_kind": None,
            "retry_at": snapshot.retry_at if snapshot.presentation_epoch == presentation_epoch else None,
        }
    ).model_dump(mode="json")
    return True


def _stale_receipt(reason: str) -> RequestError:
    return RequestError(
        status_code=409,
        message="静态呈现回执已过期或不属于当前显示方式",
        code="static_presentation_expired",
        details={"reason": reason},
    )


async def record_static_receipt(user_id: int, body: StageReceipt) -> None:
    outfit_changed = False
    async with SESSION_LOCAL() as db:
        state = await require_presentation_row(db, user_id, lock=True)
        raw = state.pending_static_json or state.current_static_json
        presence = get_presentation_snapshot(user_id)
        if not raw:
            raise _stale_receipt("target_replaced")
        snapshot = StagePresentation.model_validate(raw)
        # 已记账的同一回执仅确认幂等，不受后续生活或在线变化影响，也不恢复旧呈现。
        if (
            state.pending_static_json is None
            and snapshot.request_id == body.request_id
            and snapshot.presentation_epoch == body.presentation_epoch
            and snapshot.receipt_kind == body.receipt_kind
            and snapshot.state_revision == body.state_revision
        ):
            return
        if presence is None:
            raise _stale_receipt("presence_expired")
        expected_kind = "window_static" if presence.mode == "window" else "desktop_static"
        if state.pending_command_json or snapshot.request_id != body.request_id:
            raise _stale_receipt("target_replaced")
        if body.presentation_epoch != presence.revision or body.presentation_epoch != snapshot.presentation_epoch:
            raise _stale_receipt("presentation_changed")
        if body.receipt_kind != expected_kind:
            raise _stale_receipt("mode_changed")
        if not get_user_proactive_record(user_id).available:
            raise _stale_receipt("stage_unavailable")
        if not await presentation_revision_matches(db, user_id, snapshot.life_revision):
            raise _stale_receipt("life_changed")
        if state.pending_static_json is not None and state.state_revision != snapshot.state_revision:
            raise _stale_receipt("state_changed")
        if (
            body.status == "failed"
            and snapshot.retry_at is not None
            and body.state_revision in {snapshot.state_revision - 1, snapshot.state_revision}
        ):
            return
        if body.state_revision != snapshot.state_revision:
            raise _stale_receipt("state_changed")
        await touch_presentation(db, user_id)
        if body.status == "failed":
            state.pending_static_json = snapshot.model_copy(
                update={
                    "state_revision": state.state_revision,
                    "receipt_kind": None,
                    "error": body.error or "静态画面加载失败",
                    "retry_at": utc_now() + timedelta(minutes=15),
                }
            ).model_dump(mode="json")
        else:
            outfit_changed = snapshot.outfit is not None and presentation_outfit(state) != snapshot.outfit
            state.current_static_json = snapshot.model_copy(
                update={
                    "receipt_kind": body.receipt_kind,
                    "error": snapshot.error if snapshot.outfit is None or snapshot.scene is None else None,
                    "retry_at": None,
                }
            ).model_dump(mode="json")
            state.pending_static_json = None
            state.current_visual_id = state.current_version_id = state.loop_visual_id = state.loop_version_id = None
            state.current_snapshot_json = state.loop_snapshot_json = None
            await db.execute(
                update(VisualPlayback)
                .where(VisualPlayback.user_id == user_id, VisualPlayback.status == "started")
                .values(status="failed", error="已由静态素材呈现接替", updated_at=utc_now())
            )
        await db.commit()
    if outfit_changed:
        request_base_motion_preparation(user_id)
