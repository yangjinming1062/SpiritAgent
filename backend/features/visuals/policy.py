"""影像自主播放的确定性资格。"""

from infrastructure.text import utc_now
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.proactive_runtime import (
    PresentationSnapshot,
    get_presentation_snapshot,
    get_user_proactive_record,
)
from features.companion.rhythm import read_rhythm

from .models import VisualItem, VisualPlayback, VisualPresentation
from .repository import displayed_rest_transition, presented_frame, visual_is_short_action
from .schemas import PresentedVisual, VisualSnapshot


async def full_stage_rest_in_progress(
    db: AsyncSession, presentation: VisualPresentation, presence: PresentationSnapshot
) -> bool:
    if presence.surface not in ("desktop", "life"):
        return False
    frame = presented_frame(presentation, presence)
    if isinstance(frame, PresentedVisual) and displayed_rest_transition(frame):
        return True
    return (await read_rhythm(db, presentation.user_id)).state == "asleep"


async def visual_autonomous_block_reason(
    db: AsyncSession,
    presentation: VisualPresentation,
    item: VisualItem | PresentedVisual,
    *,
    excluded_play_id: str | None = None,
) -> str | None:
    if not presentation.autonomous_enabled:
        return "disabled"
    presence = get_presentation_snapshot(presentation.user_id)
    record = get_user_proactive_record(presentation.user_id)
    if presence is None or not record.available:
        return "unavailable"
    if item.asset_kind in ("cinematic", "scene") and await full_stage_rest_in_progress(db, presentation, presence):
        return "rest_transition_in_progress"
    if presentation.current_visual_id == item.id and presentation.current_snapshot_json:
        current = PresentedVisual.model_validate(presentation.current_snapshot_json)
        if current.version == item.version:
            return "already_current"
    commands = select(VisualPlayback.snapshot_json).where(
        VisualPlayback.user_id == presentation.user_id,
        or_(
            and_(
                VisualPlayback.status.in_(("queued", "claimed")),
                VisualPlayback.expires_at > utc_now(),
                VisualPlayback.presentation_epoch == presentation.presentation_epoch,
                VisualPlayback.state_revision == presentation.state_revision,
            ),
            and_(VisualPlayback.status == "started", VisualPlayback.visual_id == presentation.current_visual_id),
        ),
    )
    if excluded_play_id is not None:
        commands = commands.where(VisualPlayback.play_id != excluded_play_id)
    for frozen in await db.scalars(commands):
        if frozen:
            target = PresentedVisual.model_validate(frozen)
            if visual_is_short_action(target, VisualSnapshot.model_validate(target.snapshot_json)):
                return "short_action_in_progress"
    return None
