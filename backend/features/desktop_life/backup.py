"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.media.contracts import RemovedActionVideoTask

from .backup_assets import restore_desktop_payload, validate_desktop_owned_paths
from .models import DesktopVideoAction, DesktopVideoSet, DesktopVideoState
from .repository import desktop_removed_video_task
from .schemas import DesktopVideoProgress

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
    if table == "desktop_video_actions" and payload.get("generation_state_json") and payload.get("stage") != "complete":
        if (
            context.mode == "merge"
            and await db.scalar(
                select(DesktopVideoAction.id).where(
                    DesktopVideoAction.user_id == context.user_id,
                    DesktopVideoAction.set_id == payload["set_id"],
                    DesktopVideoAction.key == payload["key"],
                ),
            )
            is not None
        ):
            return
        suite = await db.get(DesktopVideoSet, payload["set_id"])
        progress = DesktopVideoProgress.model_validate_json(payload["generation_state_json"])
        if suite is None or (progress.target_width, progress.target_height, progress.resolution_revision) != (
            suite.target_width,
            suite.target_height,
            suite.resolution_revision,
        ):
            raise ValueError("桌景制作草稿的目标分辨率与套装不一致。")
    if table == "desktop_video_states":
        selected = None
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
            if field == "selected_action_id":
                selected = action
        if selected is not None and selected.kind == "once":
            payload["selected_action_id"] = payload.get("loop_action_id")


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
            unique_keys=("request_id",),
            strict_references=False,
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


async def desktop_video_tasks_for_removal(db: AsyncSession, user_id: int) -> tuple[RemovedActionVideoTask, ...]:
    actions = await db.scalars(select(DesktopVideoAction).where(DesktopVideoAction.user_id == user_id))
    return tuple(task for action in actions if (task := desktop_removed_video_task(action)) is not None)


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
