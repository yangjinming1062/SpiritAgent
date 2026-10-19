"""跨业务读取桌面生活运行态的公开快照。"""

import asyncio

from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.identity import source_visual_key
from features.action_library.schemas import ActionSourceSnapshot

from .models import DesktopVideoAction, DesktopVideoSet
from .repository import (
    desktop_accepted_asset,
    desktop_asset_description,
    desktop_base_blocked_reason,
    desktop_progress,
    desktop_state,
)
from .schemas import DesktopVisualSnapshot


def _source_readiness(snapshot: DesktopVisualSnapshot, expected_key: str) -> str | None:
    key = source_visual_key(
        snapshot.identity_hash,
        snapshot.outfit_hash,
        portrait_hash=snapshot.portrait_hash,
        identity_reference_role=snapshot.identity_reference_role,
    )
    if expected_key != key:
        return "桌景冻结身份与着装来源不匹配"
    for reference, expected_hash in (
        (snapshot.identity_path, snapshot.identity_hash),
        (snapshot.portrait_path, snapshot.portrait_hash),
        (snapshot.outfit_path, snapshot.outfit_hash),
        (snapshot.scene_path, snapshot.scene_hash),
    ):
        resolved = resolve_asset_reference(reference)
        if resolved is None:
            return "桌景冻结参考素材缺失，请恢复原素材"
        try:
            if compute_file_sha256(resolved[0]) != expected_hash:
                return "桌景冻结参考素材已变化，请重新创建桌景"
        except OSError:
            return "桌景冻结参考素材无法读取，请恢复原素材"
    return None


async def desktop_action_source(
    db: AsyncSession,
    user_id: int,
    set_id: int,
) -> ActionSourceSnapshot | None:
    suite = await db.scalar(
        select(DesktopVideoSet).where(DesktopVideoSet.id == set_id, DesktopVideoSet.user_id == user_id),
    )
    if suite is None:
        return None
    snapshot = DesktopVisualSnapshot.model_validate_json(suite.context_json)
    reason = await asyncio.to_thread(_source_readiness, snapshot, suite.source_visual_key)
    reason = reason or await desktop_base_blocked_reason(db, suite)
    return ActionSourceSnapshot(
        user_id=user_id,
        outfit_id=suite.outfit_id,
        target_kind="desktop",
        target_id=suite.id,
        label=suite.title,
        source_visual_key=suite.source_visual_key,
        ready=reason is None,
        blocked_reason=reason,
        reference_paths=[snapshot.primary_reference_path, snapshot.outfit_path, snapshot.scene_path],
        reference_roles=[snapshot.identity_reference_role, "outfit", "scene"],
        identity=snapshot.identity,
        outfit_description=snapshot.outfit_description,
        persona=snapshot.persona,
    )


async def desktop_action_generation_blocked_reason(
    db: AsyncSession,
    action: DesktopVideoAction,
    suite: DesktopVideoSet,
) -> str | None:
    if action.key == "idle":
        return None
    if action.definition_id is None:
        return await desktop_base_blocked_reason(db, suite)
    source = await desktop_action_source(db, action.user_id, suite.id)
    return source.blocked_reason if source is not None else "桌景已删除"


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
        "action": {"id": action.id, "name": action.name, "description": desktop_asset_description(action, asset)}
        if action is not None and asset is not None
        else None,
    }
