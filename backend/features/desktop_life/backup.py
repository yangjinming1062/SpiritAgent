"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .backup_assets import restore_desktop_payload, validate_desktop_owned_paths
from .models import DesktopVideoAction, DesktopVideoSet, DesktopVideoState

DESKTOP_TABLES = frozenset({"desktop_video_sets", "desktop_video_actions", "desktop_video_states"})


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    id_map = context.id_map
    if table in DESKTOP_TABLES:
        restore_desktop_payload(table, payload, id_map)


async def _check(
    table: str,
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> None:
    validate_desktop_owned_paths(payload, context.asset_owner_id)
    if table == "desktop_video_states":
        for field in ("selected_action_id", "loop_action_id"):
            if payload.get(field) is None:
                continue
            action = await db.get(DesktopVideoAction, payload[field])
            if (
                action is None
                or action.user_id != context.user_id
                or action.set_id != payload.get("current_set_id")
                or field == "loop_action_id"
                and action.kind != "loop"
            ):
                raise ValueError("桌面生活播放选择不属于当前组合。")


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "desktop_video_sets",
            DesktopVideoSet,
            foreign_keys={
                "avatar_id": "avatar_assets",
                "outfit_id": "companion_outfits",
                "scene_id": "companion_scenes",
            },
            unique_keys=("context_hash",),
            strict_references=True,
            merge_unique_on_overwrite=True,
            prepare_payload=partial(_prepare, "desktop_video_sets"),
            validate_payload=partial(_check, "desktop_video_sets"),
        ),
        BackupTable(
            "desktop_video_actions",
            DesktopVideoAction,
            foreign_keys={"set_id": "desktop_video_sets"},
            unique_keys=("set_id", "key"),
            strict_references=True,
            merge_unique_on_overwrite=True,
            prepare_payload=partial(_prepare, "desktop_video_actions"),
            validate_payload=partial(_check, "desktop_video_actions"),
        ),
        BackupTable(
            "desktop_video_states",
            DesktopVideoState,
            foreign_keys={
                "current_set_id": "desktop_video_sets",
                "selected_action_id": "desktop_video_actions",
                "loop_action_id": "desktop_video_actions",
            },
            unique_keys=(),
            strict_references=True,
            prepare_payload=partial(_prepare, "desktop_video_states"),
            validate_payload=partial(_check, "desktop_video_states"),
        ),
    )


async def playback_versions(db: AsyncSession, user_id: int) -> tuple[int, int] | None:
    row = (
        await db.execute(
            select(DesktopVideoState.version, DesktopVideoState.set_epoch).where(DesktopVideoState.user_id == user_id),
        )
    ).one_or_none()
    return tuple(row) if row is not None else None


async def advance_playback_versions(db: AsyncSession, user_id: int, previous: tuple[int, int] | None) -> None:
    state = await db.scalar(select(DesktopVideoState).where(DesktopVideoState.user_id == user_id))
    if state is None:
        return
    version, epoch = previous or (0, 0)
    state.version = max(state.version, version) + 1
    state.set_epoch = max(state.set_epoch, epoch) + 1
    state.selected_play_id = None
    emit_ws_event(db, user_id=user_id, event_type="companion.desktop_video.updated", payload={"version": state.version})
