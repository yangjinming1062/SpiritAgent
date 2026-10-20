"""跨业务读取桌面生活运行态的公开快照。"""

import asyncio

from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.identity import source_visual_key
from features.action_library.schemas import ActionSourceSnapshot
from features.companion.proactive_runtime import get_presentation_snapshot

from .models import DesktopVideoAction, DesktopVideoSet
from .repository import (
    desktop_accepted_asset,
    desktop_asset_description,
    desktop_base_blocked_reason,
    desktop_progress,
    desktop_state,
    desktop_upload_source_error,
)
from .schemas import DesktopContext, DesktopUploadSnapshot, DesktopVisualSnapshot, parse_desktop_context


def _source_readiness(snapshot: DesktopContext, expected_key: str) -> str | None:
    if isinstance(snapshot, DesktopUploadSnapshot):
        if snapshot.source_key != expected_key:
            return "桌景上传来源不匹配，请重新创建桌景"
        return desktop_upload_source_error(snapshot)
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
    snapshot = parse_desktop_context(suite.source, suite.context_json)
    reason = await asyncio.to_thread(_source_readiness, snapshot, suite.source_visual_key)
    reason = reason or await desktop_base_blocked_reason(db, suite)
    outfit_description = snapshot.outfit_description
    if isinstance(snapshot, DesktopUploadSnapshot):
        idle = await db.scalar(
            select(DesktopVideoAction).where(
                DesktopVideoAction.user_id == user_id,
                DesktopVideoAction.set_id == set_id,
                DesktopVideoAction.key == "idle",
            ),
        )
        asset = desktop_accepted_asset(idle) if idle is not None else None
        paths = [asset.image_path] if asset is not None else []
        roles = ["opening"] if paths else []
        outfit_description = (
            _upload_outfit_description(snapshot, asset.image_path, asset.outfit_description)
            if asset is not None
            else ""
        )
    else:
        paths = [snapshot.primary_reference_path, snapshot.outfit_path, snapshot.scene_path]
        roles = [snapshot.identity_reference_role, "outfit", "scene"]
    return ActionSourceSnapshot(
        user_id=user_id,
        outfit_id=suite.outfit_id,
        target_kind="desktop",
        target_id=suite.id,
        label=suite.title,
        source_visual_key=suite.source_visual_key,
        ready=reason is None,
        blocked_reason=reason,
        scope="target" if isinstance(snapshot, DesktopUploadSnapshot) else "outfit",
        reference_paths=paths,
        reference_roles=roles,
        identity=snapshot.identity if isinstance(snapshot, DesktopVisualSnapshot) else None,
        outfit_description=outfit_description,
        persona=snapshot.persona,
    )


def _upload_outfit_description(snapshot: DesktopUploadSnapshot, image_path: str, description: str | None) -> str:
    if description is not None:
        return description
    return snapshot.outfit_description if image_path == snapshot.reference_image_path else ""


async def desktop_action_generation_blocked_reason(
    db: AsyncSession,
    action: DesktopVideoAction,
    suite: DesktopVideoSet,
) -> str | None:
    if suite.source != "ai":
        snapshot = parse_desktop_context(suite.source, suite.context_json)
        if reason := await asyncio.to_thread(_source_readiness, snapshot, suite.source_visual_key):
            return reason
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


async def _activated_desktop_visual(
    db: AsyncSession,
    user_id: int,
) -> tuple[DesktopContext, str, str, str, dict[str, object] | None] | None:
    state = await desktop_state(db, user_id)
    suite = await db.get(DesktopVideoSet, state.current_set_id) if state and state.current_set_id else None
    if suite is None or suite.user_id != user_id:
        return None
    snapshot = parse_desktop_context(suite.source, suite.context_json)
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
    image_path = asset.image_path if asset else confirmed_draft.pose_path if confirmed_draft else ""
    outfit_description = snapshot.outfit_description
    if isinstance(snapshot, DesktopUploadSnapshot):
        actual_outfit = (
            asset.outfit_description if asset else confirmed_draft.outfit_description if confirmed_draft else None
        )
        outfit_description = _upload_outfit_description(snapshot, image_path, actual_outfit)
    action_summary: dict[str, object] | None = (
        {"id": action.id, "name": action.name, "description": desktop_asset_description(action, asset)}
        if action is not None and asset is not None
        else None
    )
    return snapshot, image_path, visual_description, outfit_description, action_summary


async def desktop_current_styling_reference(db: AsyncSession, user_id: int) -> tuple[str, str] | None:
    """窗口返回 None；桌景缺少已确认画面时返回空路径，禁止回退衣柜当前项。"""
    presentation = get_presentation_snapshot(user_id)
    if presentation is None or presentation.mode != "desktop":
        return None
    current = await _activated_desktop_visual(db, user_id)
    if current is None or not current[1]:
        return "", ""
    _, image_path, _, outfit_description, _ = current
    return image_path, outfit_description


async def activated_desktop_environment(db: AsyncSession, user_id: int) -> dict[str, object]:
    """桌面环境只读取显式启用套装，来源库的当前指针和后续修改不参与。"""
    current = await _activated_desktop_visual(db, user_id)
    if current is None:
        return {"presentation_mode": "desktop", "current": None}
    snapshot, image_path, visual_description, outfit_description, action_summary = current
    return {
        "presentation_mode": "desktop",
        "current": {
            "description": visual_description,
            "outfit_description": outfit_description,
            "image_is_authority": True,
        }
        if image_path
        else None,
        "creation_reference": {
            "scene_description": snapshot.scene_description,
            "outfit_description": snapshot.outfit_description,
            "usage": "creation_provenance_only",
        }
        if isinstance(snapshot, DesktopVisualSnapshot)
        else {"source": "uploaded", "usage": "creation_provenance_only"},
        "action": action_summary,
    }
