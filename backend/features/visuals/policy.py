"""影像自主播放的确定性资格；冷却只依据实际开播事实。"""

from datetime import datetime, timedelta

from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record

from .models import VisualItem, VisualPlayback, VisualPresentation
from .repository import visual_is_short_action, visual_plays_as_loop
from .schemas import PresentedVisual, VisualSnapshot

LOOP_HOLD_SECONDS = 10 * 60
ONCE_REPEAT_SECONDS = 30 * 60

# 只呈现过（开播或以实际呈现结账）的回执才参与冷却计算。
_PRESENTED_STATES = ("started", "finished")


def _presented_at(play: VisualPlayback) -> datetime:
    return ensure_utc(play.started_at or play.updated_at)


async def visual_autonomous_block_reason(
    db: AsyncSession,
    presentation: VisualPresentation,
    item: VisualItem | PresentedVisual,
    *,
    snapshot: VisualSnapshot | None = None,
    excluded_play_id: str | None = None,
) -> str | None:
    if not presentation.autonomous_enabled:
        return "disabled"
    presence = get_presentation_snapshot(presentation.user_id)
    record = get_user_proactive_record(presentation.user_id)
    if presence is None or not record.available:
        return "unavailable"
    if presentation.current_visual_id == item.id and presentation.current_snapshot_json:
        current = PresentedVisual.model_validate(presentation.current_snapshot_json)
        if current.version == item.version:
            return "already_current"
    snapshot = snapshot if snapshot is not None else VisualSnapshot.model_validate(item.snapshot_json)
    now = utc_now()
    plays = [
        play
        for play in await db.scalars(
            select(VisualPlayback)
            .where(VisualPlayback.user_id == presentation.user_id)
            .order_by(VisualPlayback.id.desc())
            .limit(64),
        )
        if play.play_id != excluded_play_id
    ]
    for play in plays:
        target = PresentedVisual.model_validate(play.snapshot_json) if play.snapshot_json else None
        if target is None or not visual_is_short_action(target, VisualSnapshot.model_validate(target.snapshot_json)):
            continue
        pending = (
            play.status in {"queued", "claimed"}
            and ensure_utc(play.expires_at) > now
            and play.presentation_epoch == presentation.presentation_epoch
            and play.state_revision == presentation.state_revision
        )
        started = play.status == "started" and play.visual_id == presentation.current_visual_id
        if pending or started:
            return "short_action_in_progress"
    if visual_plays_as_loop(item, snapshot) and presentation.loop_visual_id not in (None, item.id):
        loop_plays = [
            play for play in plays if play.visual_id == presentation.loop_visual_id and play.status in _PRESENTED_STATES
        ]
        if loop_plays and now - max(_presented_at(play) for play in loop_plays) < timedelta(seconds=LOOP_HOLD_SECONDS):
            return "loop_hold"
    if visual_is_short_action(item, snapshot):
        recent = next(
            (
                play
                for play in plays
                if play.visual_id == item.id
                and play.source == "autonomous"
                and play.status in _PRESENTED_STATES
                and now - _presented_at(play) < timedelta(seconds=ONCE_REPEAT_SECONDS)
            ),
            None,
        )
        if recent is not None:
            return "repeat_cooldown"
    return None
