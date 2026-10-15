"""跨业务读取桌面生活运行态的公开快照。"""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DesktopVideoAction, DesktopVideoSet
from .repository import desktop_accepted_asset, desktop_progress, desktop_state
from .schemas import DesktopVisualSnapshot


class DesktopStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    user_id: int
    current_set_id: int | None
    selected_action_id: int | None
    selected_play_id: str | None
    loop_action_id: int | None
    set_epoch: int
    version: int
    pinned: bool
    autonomous_enabled: bool
    preparation_error: str | None


async def desktop_state_snapshot(db: AsyncSession, user_id: int) -> DesktopStateSnapshot | None:
    row = await desktop_state(db, user_id)
    return DesktopStateSnapshot.model_validate(row) if row is not None else None


async def activated_desktop_environment(db: AsyncSession, user_id: int) -> dict[str, object]:
    """桌面环境只读取显式启用套装，来源库的当前指针和后续修改不参与。"""
    state = await desktop_state(db, user_id)
    suite = await db.get(DesktopVideoSet, state.current_set_id) if state and state.current_set_id else None
    if suite is None or suite.user_id != user_id:
        return {"presentation_mode": "desktop", "current": None}
    snapshot = DesktopVisualSnapshot.model_validate_json(suite.context_json)
    ids = [action_id for action_id in (state.selected_action_id, state.loop_action_id) if action_id is not None]
    rows = list(
        await db.scalars(
            select(DesktopVideoAction).where(
                DesktopVideoAction.user_id == user_id,
                DesktopVideoAction.set_id == suite.id,
                or_(DesktopVideoAction.id.in_(ids), DesktopVideoAction.key == "idle"),
            ),
        ),
    )
    by_id = {row.id: row for row in rows}
    idle = next((row for row in rows if row.key == "idle"), None)
    ordered = [by_id[action_id] for action_id in dict.fromkeys(ids) if action_id in by_id]
    if idle is not None and idle not in ordered:
        ordered.append(idle)
    action, asset = None, None
    for candidate in ordered:
        if candidate.kind == "once" and state.selected_play_id is None:
            continue
        if not candidate.enabled or candidate.needs_update:
            continue
        accepted = desktop_accepted_asset(candidate)
        if accepted is not None:
            action, asset = candidate, accepted
            break
    progress = None
    if idle is not None and idle.enabled and not idle.needs_update and idle.stage != "complete":
        progress = desktop_progress(idle)
    confirmed_draft = progress if asset is None and progress is not None and progress.image_confirmed else None
    visual_description = (
        asset.visual_description if asset else confirmed_draft.visual_description if confirmed_draft else ""
    )
    return {
        "presentation_mode": "desktop",
        "current": {"description": visual_description, "image_is_authority": True},
        "creation_reference": {
            "scene_description": snapshot.scene_description,
            "outfit_description": snapshot.outfit_description,
            "usage": "creation_provenance_only",
        },
        "action": {"id": action.id, "name": action.name, "description": action.description}
        if action is not None and asset is not None
        else None,
    }
