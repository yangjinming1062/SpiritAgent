"""影像身份、不可变版本与播放账本的物理表备份。"""

from copy import deepcopy
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import BigInteger, column, select, update, values
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import rewrite_job_checkpoints
from features.media.chain import MediaChainState
from features.media.contracts import RemovedVideoTask

from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion, VisualVersionAsset
from .repository import version_view, visual_progress
from .schemas import ActorClipRef, StageComposition, StageIntent, StagePresentation, VisualSnapshot, parse_job_progress


def _prepare_presentation(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["intent_json"]:
        payload["intent_json"] = StageIntent.model_validate(payload["intent_json"]).model_dump(mode="json")
    payload["pending_command_json"] = None
    payload["pending_static_json"] = None
    if payload["current_static_json"]:
        payload["current_static_json"] = StagePresentation.model_validate(payload["current_static_json"]).model_dump(
            mode="json"
        )
    payload["current_snapshot_json"] = None
    payload["loop_snapshot_json"] = None
    payload["state_revision"] += 1
    payload["presentation_epoch"] += 1


def _prepare_playback(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload["status"] in {"queued", "claimed", "started"}:
        payload["status"] = "interrupted"
    payload["client_id"] = None
    payload["expires_at"] = utc_now()


def _prepare_version(raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    snapshot = VisualSnapshot.model_validate(payload["snapshot_json"])
    payload["snapshot_json"] = snapshot.model_dump(mode="json")


def _mapped(context: BackupContext, table: str, value: int | str | None) -> int | None:
    mapped = context.id_map.get(table, {}).get(str(value)) if value is not None else None
    return int(mapped) if mapped is not None else None


def _remap_clip_ref(context: BackupContext, ref: ActorClipRef | None) -> ActorClipRef | None:
    if ref is None:
        return None
    visual_id = _mapped(context, "visual_items", ref.visual_id)
    return ref.model_copy(update={"visual_id": visual_id}) if visual_id is not None else None


def _remap_snapshot(value: dict, context: BackupContext) -> dict:
    if not value:
        return {}
    snapshot = VisualSnapshot.model_validate(context.rewriter.rewrite(deepcopy(value)))
    source = snapshot.source
    update: dict[str, Any] = {}
    if source.kind in ("actor", "cinematic"):
        update["identity"] = source.identity.model_copy(
            update={"avatar_id": _mapped(context, "avatar_assets", source.identity.avatar_id)}
        )
        update["outfit"] = source.outfit.model_copy(
            update={"material_id": _mapped(context, "companion_outfits", source.outfit.material_id)}
        )
        update["action"] = source.action.model_copy(
            update={"action_id": _mapped(context, "action_definitions", source.action.action_id)}
        )
    if source.kind in ("scene", "cinematic"):
        update["scene"] = source.scene.model_copy(
            update={"scene_id": _mapped(context, "companion_scenes", source.scene.scene_id)}
        )
    return snapshot.model_copy(update={"source": source.model_copy(update=update)}).model_dump(mode="json")


async def _rewrite_snapshots(
    db: AsyncSession,
    model: type[VisualVersion] | type[VisualJob],
    rows: dict[str, list[dict[str, Any]]],
    context: BackupContext,
) -> None:
    table = model.__table__
    patches = [
        (context.id_map[table.name][str(raw["id"])], _remap_snapshot(raw["snapshot_json"], context))
        for raw in rows.get(table.name, [])
        if (table.name, str(raw["id"])) in context.rewriter.inserted_rows
    ]
    owner = table.c.user_id if "user_id" in table.c else table.c.owner_id
    for start in range(0, len(patches), 500):
        payload = (
            values(column("row_id", BigInteger), column("snapshot", JSONB))
            .data(patches[start : start + 500])
            .alias("restored_snapshots")
        )
        await db.execute(
            update(table)
            .where(owner == context.user_id, table.c.id == payload.c.row_id)
            .values(snapshot_json=payload.c.snapshot)
        )


async def _restore_versions(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    await _rewrite_snapshots(db, VisualVersion, rows, context)


async def _restore_playbacks(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    ids = [
        context.id_map["visual_playbacks"][str(raw["id"])]
        for raw in rows.get("visual_playbacks", [])
        if ("visual_playbacks", str(raw["id"])) in context.rewriter.inserted_rows
    ]
    if not ids:
        return
    records = await db.execute(
        select(VisualPlayback, VisualVersion, VisualItem)
        .join(
            VisualVersion,
            VisualVersion.id == VisualPlayback.version_id,
        )
        .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
        .where(VisualPlayback.id.in_(ids), VisualPlayback.user_id == context.user_id)
    )
    patches = [
        (
            play.id,
            version_view(item, version)
            .model_copy(update={"play_id": play.play_id, "receipt_kind": play.receipt_kind})
            .model_dump(mode="json"),
        )
        for play, version, item in records
    ]
    for start in range(0, len(patches), 500):
        payload = (
            values(column("row_id", BigInteger), column("snapshot", JSONB))
            .data(patches[start : start + 500])
            .alias("restored_plays")
        )
        await db.execute(
            update(VisualPlayback.__table__)
            .where(VisualPlayback.user_id == context.user_id, VisualPlayback.id == payload.c.row_id)
            .values(snapshot_json=payload.c.snapshot)
        )


async def _restore_presentation_snapshots(
    db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext
) -> None:
    states = list(await db.scalars(select(VisualPresentation).where(VisualPresentation.user_id == context.user_id)))
    for state in states:
        if state.composition_json:
            composition = StageComposition.model_validate(context.rewriter.rewrite(deepcopy(state.composition_json)))
            actor = _remap_clip_ref(context, composition.actor)
            cinematic = _remap_clip_ref(context, composition.cinematic)
            scene_id = _mapped(context, "visual_items", composition.scene_visual_id)
            state.composition_json = (
                composition.model_copy(
                    update={
                        "actor": actor,
                        "cinematic": cinematic,
                        "scene_visual_id": scene_id,
                        "scene_version": composition.scene_version if scene_id is not None else None,
                        "outfit_material": composition.outfit_material.model_copy(
                            update={
                                "material_id": _mapped(
                                    context, "companion_outfits", composition.outfit_material.material_id
                                )
                            }
                        )
                        if composition.outfit_material
                        else None,
                        "scene_material": composition.scene_material.model_copy(
                            update={
                                "scene_id": _mapped(context, "companion_scenes", composition.scene_material.scene_id)
                            }
                        )
                        if composition.scene_material
                        else None,
                        "media": None,
                    }
                ).model_dump(mode="json")
                if composition.mode == "layered" or cinematic is not None
                else None
            )
        if state.current_static_json:
            static = StagePresentation.model_validate(context.rewriter.rewrite(deepcopy(state.current_static_json)))
            state.current_static_json = static.model_copy(
                update={
                    "outfit": static.outfit.model_copy(
                        update={"material_id": _mapped(context, "companion_outfits", static.outfit.material_id)}
                    )
                    if static.outfit
                    else None,
                    "scene": static.scene.model_copy(
                        update={"scene_id": _mapped(context, "companion_scenes", static.scene.scene_id)}
                    )
                    if static.scene
                    else None,
                    "presentation_epoch": state.presentation_epoch,
                    "state_revision": state.state_revision,
                    "receipt_kind": None,
                }
            ).model_dump(mode="json")
    version_ids = {
        value for row in states for value in (row.current_version_id, row.loop_version_id) if value is not None
    }
    if not version_ids:
        return
    versions = list(
        (
            await db.execute(
                select(VisualVersion, VisualItem)
                .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
                .where(VisualVersion.id.in_(version_ids), VisualVersion.user_id == context.user_id)
            )
        ).all()
    )
    frozen = {}
    for version, item in versions:
        frozen[version.id] = version_view(item, version).model_dump(mode="json")
    for state in states:
        state.current_snapshot_json = frozen.get(state.current_version_id)
        state.loop_snapshot_json = frozen.get(state.loop_version_id)


def _clear_remote_handles(checkpoint: dict | None) -> dict | None:
    progress = parse_job_progress(checkpoint)
    if progress is None:
        return None
    if progress.video_chain_json:
        chain = MediaChainState.model_validate_json(progress.video_chain_json)
        chain.queue_request_id = None
        chain.video_queue = None
        progress.video_chain_json = chain.model_dump_json()
    progress.video_queue = None
    return progress.model_dump(mode="json")


async def _restore_jobs(db: AsyncSession, rows: dict[str, list[dict[str, Any]]], context: BackupContext) -> None:
    await _rewrite_snapshots(db, VisualJob, rows, context)
    ids = [
        context.id_map["visual_jobs"][str(raw["id"])]
        for raw in rows.get("visual_jobs", [])
        if ("visual_jobs", str(raw["id"])) in context.rewriter.inserted_rows
    ]
    await rewrite_job_checkpoints(db, context.user_id, ids, _clear_remote_handles)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("visual_items", VisualItem, unique_keys=("source_key",)),
        BackupTable(
            "visual_versions",
            VisualVersion,
            unique_keys=("visual_id", "number"),
            prepare_payload=_prepare_version,
            restore_context=_restore_versions,
        ),
        BackupTable("visual_version_assets", VisualVersionAsset, unique_keys=("version_id", "role", "asset_id")),
        BackupTable("visual_jobs", VisualJob, restore_context=_restore_jobs),
        BackupTable(
            "visual_presentations",
            VisualPresentation,
            unique_keys=(),
            prepare_payload=_prepare_presentation,
            restore_context=_restore_presentation_snapshots,
        ),
        BackupTable(
            "visual_playbacks",
            VisualPlayback,
            unique_keys=("play_id",),
            prepare_payload=_prepare_playback,
            restore_context=_restore_playbacks,
        ),
    )


def _removed_task(job: VisualJob) -> RemovedVideoTask | None:
    progress = visual_progress(job)
    if progress is None or not progress.video_chain_json:
        return None
    return RemovedVideoTask(task_id=job.id, generation_state_json=progress.video_chain_json)


async def visual_video_tasks_for_removal(db: AsyncSession, user_id: int) -> tuple[RemovedVideoTask, ...]:
    jobs = await db.scalars(select(VisualJob).where(VisualJob.user_id == user_id))
    return tuple(task for job in jobs if (task := _removed_task(job)) is not None)


async def playback_versions(db: AsyncSession, user_id: int) -> tuple[int, int] | None:
    row = (
        await db.execute(
            select(VisualPresentation.state_revision, VisualPresentation.presentation_epoch).where(
                VisualPresentation.user_id == user_id,
            ),
        )
    ).one_or_none()
    return tuple(row) if row is not None else None


async def advance_playback_versions(db: AsyncSession, user_id: int, previous: tuple[int, int] | None) -> None:
    """恢复或换号后推进呈现版本，旧待播指令不再被认领。"""

    state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
    if state is None:
        return
    revision, epoch = previous or (0, 0)
    state.state_revision = max(state.state_revision, revision) + 1
    state.presentation_epoch = max(state.presentation_epoch, epoch) + 1
    state.pending_command_json = None
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": state.state_revision})
