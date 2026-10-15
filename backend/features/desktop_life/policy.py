"""桌景自主播放的确定性资格；冷却只依据实际开播事实。"""

from datetime import timedelta

from infrastructure.text import utc_now
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.desktop_life.models import DesktopVideoAction, DesktopVideoPlayback, DesktopVideoSet, DesktopVideoState
from features.desktop_life.schemas import DesktopVideoAsset

LOOP_HOLD_SECONDS = 10 * 60
ONCE_REPEAT_SECONDS = 30 * 60


async def desktop_autonomous_block_reason(
    db: AsyncSession,
    state: DesktopVideoState,
    action: DesktopVideoAction,
    asset: DesktopVideoAsset,
    *,
    presentation_revision: int,
    excluded_play_id: str | None = None,
) -> str | None:
    if action.needs_update:
        return "base_mismatch"
    if state.pinned or not state.autonomous_enabled:
        return "disabled"
    now = utc_now()
    pending_once = await db.scalar(
        select(DesktopVideoPlayback.id)
        .join(DesktopVideoAction, DesktopVideoAction.id == DesktopVideoPlayback.action_id)
        .where(
            DesktopVideoPlayback.user_id == state.user_id,
            DesktopVideoPlayback.set_id == state.current_set_id,
            DesktopVideoPlayback.set_epoch == state.set_epoch,
            DesktopVideoPlayback.play_id != excluded_play_id if excluded_play_id else True,
            DesktopVideoAction.kind == "once",
            or_(
                and_(
                    DesktopVideoPlayback.status.in_(("queued", "claimed")),
                    DesktopVideoPlayback.expires_at > now,
                    DesktopVideoPlayback.presentation_revision == presentation_revision,
                ),
                and_(
                    DesktopVideoPlayback.status == "started",
                    DesktopVideoPlayback.play_id == state.selected_play_id,
                    DesktopVideoPlayback.action_id == state.selected_action_id,
                ),
            ),
        )
        .limit(1),
    )
    if pending_once is not None:
        return "short_action_in_progress"
    if action.kind == "loop":
        if action.id != state.loop_action_id and (
            state.loop_changed_at is None or now - state.loop_changed_at < timedelta(seconds=LOOP_HOLD_SECONDS)
        ):
            return "loop_hold"
        return None
    recent = await db.scalar(
        select(DesktopVideoPlayback.id)
        .where(
            DesktopVideoPlayback.user_id == state.user_id,
            DesktopVideoPlayback.action_id == action.id,
            DesktopVideoPlayback.source == "autonomous",
            DesktopVideoPlayback.started_at >= now - timedelta(seconds=ONCE_REPEAT_SECONDS),
        )
        .limit(1),
    )
    if recent is not None:
        return "repeat_cooldown"
    current_set = await db.get(DesktopVideoSet, state.current_set_id)
    loop = await db.get(DesktopVideoAction, state.loop_action_id) if state.loop_action_id is not None else None
    if current_set is None or loop is None or loop.key != "idle" or loop.accepted_asset_json is None:
        return "base_mismatch"
    loop_asset = DesktopVideoAsset.model_validate_json(loop.accepted_asset_json)
    if (
        asset.continuity != "base"
        or asset.base_version != current_set.base_version
        or loop_asset.base_version != current_set.base_version
    ):
        return "base_mismatch"
    return None
