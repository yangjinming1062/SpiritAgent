"""影像库的行锁、状态读写、播放账本与成品发布；生成流程由应用层管理。"""

from datetime import timedelta
from uuid import uuid4

from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from features.assets.lifecycle import enqueue_asset_cleanup
from features.companion.proactive_runtime import get_presentation_snapshot

from .asset_references import live_asset_paths
from .client_assets import client_play_command
from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation, VisualVersion
from .schemas import (
    ActionClipKind,
    ComboMaterialRef,
    PlaybackSource,
    PlaybackStatus,
    PresentedVisual,
    StageComposition,
    StagePresentation,
    VisualJobProgress,
    VisualMediaKind,
    VisualPlayCommand,
    VisualSnapshot,
    parse_job_progress,
)


class VisualError(RuntimeError):
    """可展示的影像库错误。"""


# 待播指令的有效期；过期后认领与回执都作废，不再补播。
DEFERRED_PLAY_INTENT_TTL_SECONDS = 15 * 60


class VisualNotFoundError(VisualError):
    pass


class VisualStateError(VisualError):
    pass


def visual_progress(job: VisualJob) -> VisualJobProgress | None:
    return parse_job_progress(job.checkpoint)


def visual_snapshot(row: VisualItem | VisualJob | PresentedVisual) -> VisualSnapshot:
    return VisualSnapshot.model_validate(row.snapshot_json)


def presentation_outfit(state: VisualPresentation) -> ComboMaterialRef | None:
    if state.current_static_json:
        return StagePresentation.model_validate(state.current_static_json).outfit
    item = presented_item(state)
    source = visual_snapshot(item).source if item is not None else None
    return source.outfit if source is not None and source.kind in ("actor", "cinematic") else None


def visual_clip_kind(
    item: VisualItem | PresentedVisual, snapshot: VisualSnapshot | None = None
) -> ActionClipKind | None:
    """播放方式来自冻结输入的动作引用；scene 资产没有动作，图片恒为持续画面。"""

    source = (snapshot or visual_snapshot(item)).source
    if item.media_kind == "image":
        return None
    if source.kind == "scene":
        return "loop"
    return source.action.clip_kind


def visual_plays_as_loop(item: VisualItem | PresentedVisual, snapshot: VisualSnapshot | None = None) -> bool:
    """图片和环境媒体是持续画面；主体/完整影像视频按动作播放方式决定循环。"""

    if item.media_kind == "image":
        return True
    return visual_clip_kind(item, snapshot or visual_snapshot(item)) != "once"


def visual_is_short_action(item: VisualItem | PresentedVisual, snapshot: VisualSnapshot | None = None) -> bool:
    if item.media_kind != "video":
        return False
    return visual_clip_kind(item, snapshot or visual_snapshot(item)) == "once"


def visual_playable(item: VisualItem | PresentedVisual) -> bool:
    return resolve_asset_reference(item.media_path) is not None


def presented_item(
    state: VisualPresentation,
    *,
    loop: bool = False,
) -> PresentedVisual | None:
    saved = state.loop_snapshot_json if loop else state.current_snapshot_json
    if not saved:
        return None
    frozen = PresentedVisual.model_validate(saved)
    if frozen.user_id != state.user_id:
        raise VisualStateError("呈现快照不属于当前账户")
    return frozen


def stage_composition(state: VisualPresentation) -> StageComposition | None:
    return StageComposition.model_validate(state.composition_json) if state.composition_json else None


def version_view(item: VisualItem, version: VisualVersion) -> PresentedVisual:
    return PresentedVisual(
        id=item.id,
        user_id=item.user_id,
        media_kind=item.media_kind,
        asset_kind=item.asset_kind,
        title=item.title,
        status=item.status,
        media_path=version.media_path,
        poster_path=version.poster_path,
        width=version.width,
        height=version.height,
        duration_ms=version.duration_ms,
        content_hash=version.content_hash,
        description=version.description,
        outfit_description=version.outfit_description,
        snapshot_json=version.snapshot_json,
        version=version.number,
        outfit_id=version.outfit_id,
        scene_id=version.scene_id,
        action_id=version.action_id,
        input_image_id=version.input_image_id,
        input_image_version_id=version.input_image_version_id,
        input_image_path=version.input_image_path,
        input_image_hash=version.input_image_hash,
        created_at=version.created_at,
        updated_at=version.created_at,
    )


async def presented_version(db: AsyncSession, user_id: int, version_id: int) -> PresentedVisual | None:
    row = (
        await db.execute(
            select(VisualItem, VisualVersion)
            .join(VisualVersion, VisualVersion.visual_id == VisualItem.id)
            .where(VisualVersion.id == version_id, VisualItem.user_id == user_id)
        )
    ).one_or_none()
    return version_view(*row) if row is not None else None


async def visual_item(db: AsyncSession, user_id: int, visual_id: int, *, lock: bool = False) -> VisualItem:
    query = select(VisualItem).where(VisualItem.id == visual_id, VisualItem.user_id == user_id)
    if lock:
        query = query.with_for_update(of=VisualItem)
    item = await db.scalar(query.execution_options(populate_existing=True))
    if item is None:
        raise VisualNotFoundError("找不到对应的成品")
    return item


async def visual_job_row(db: AsyncSession, user_id: int, job_id: int, *, lock: bool = False) -> VisualJob:
    query = select(VisualJob).where(VisualJob.id == job_id, VisualJob.user_id == user_id)
    if lock:
        query = query.with_for_update()
    job = await db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise VisualNotFoundError("找不到对应的制作工作项")
    return job


async def publication_rows(db: AsyncSession, user_id: int, job_id: int) -> tuple[VisualJob, VisualPresentation]:
    query = (
        select(VisualJob, VisualPresentation)
        .join(
            VisualPresentation,
            VisualPresentation.user_id == VisualJob.user_id,
        )
        .where(VisualJob.id == job_id, VisualJob.user_id == user_id)
        .with_for_update(of=(VisualJob, VisualPresentation))
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        await require_presentation_row(db, user_id)
        row = (await db.execute(query)).one_or_none()
    if row is None:
        raise VisualNotFoundError("找不到对应的制作工作项")
    return row[0], row[1]


async def presentation_row(
    db: AsyncSession,
    user_id: int,
    *,
    create: bool = False,
    lock: bool = False,
) -> VisualPresentation | None:
    if not lock:
        existing = await db.get(VisualPresentation, user_id)
        if existing is not None or not create:
            return existing
    if create:
        await db.execute(
            insert(VisualPresentation).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"])
        )
    query = select(VisualPresentation).where(VisualPresentation.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query.execution_options(populate_existing=True))


async def require_presentation_row(db: AsyncSession, user_id: int, *, lock: bool = False) -> VisualPresentation:
    """写入路径取账户呈现行，不存在时创建；返回的行必定可用。"""

    row = await presentation_row(db, user_id, create=True, lock=lock)
    if row is None:
        raise VisualStateError("呈现状态不可用，请稍后重试")
    return row


async def playback_row(db: AsyncSession, user_id: int, play_id: str) -> VisualPlayback:
    play = await db.scalar(
        select(VisualPlayback)
        .where(VisualPlayback.user_id == user_id, VisualPlayback.play_id == play_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    )
    if play is None:
        raise VisualNotFoundError("找不到对应的播放")
    return play


async def touch_presentation(db: AsyncSession, user_id: int) -> VisualPresentation:
    """呈现状态变化统一走这里：递增修订并广播，待播指令据此重新校验。"""

    state = await require_presentation_row(db, user_id)
    state.state_revision += 1
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": state.state_revision})
    return state


async def notify_visuals_updated(db: AsyncSession, user_id: int) -> None:
    """库与工作项变化只通知刷新，不推进呈现修订，避免打断在途播放校验。"""

    state = await db.get(VisualPresentation, user_id)
    if state is None:
        state = await require_presentation_row(db, user_id)
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": state.state_revision})


async def dispatch_play_command(
    db: AsyncSession,
    user_id: int,
    item: VisualItem | PresentedVisual,
    *,
    source: PlaybackSource,
    version_id: int | None = None,
    snapshot: VisualSnapshot | None = None,
) -> tuple[VisualPlayback, VisualPlayCommand]:
    """登记待执行指令；实际呈现指针只在已认领的开播回执中推进。"""

    snapshot = snapshot or visual_snapshot(item)
    presentation = get_presentation_snapshot(user_id)
    if presentation is None:
        raise VisualStateError("桌面客户端当前不可用，无法播放")
    state = await db.get(VisualPresentation, user_id)
    if state is None:
        state = await require_presentation_row(db, user_id)
    revision = state.state_revision + 1
    now = utc_now()
    media_kind: VisualMediaKind = "image" if item.media_kind == "image" else "video"
    play_id = uuid4().hex
    expires_at = now + timedelta(seconds=DEFERRED_PLAY_INTENT_TTL_SECONDS)
    payload = item.model_dump(include=set(PresentedVisual.model_fields)) if isinstance(item, PresentedVisual) else item
    frozen = PresentedVisual.model_validate(payload).model_copy(update={"play_id": play_id})
    previous = presented_item(state, loop=True)
    natural = (
        previous is not None
        and previous.media_kind == "video"
        and item.media_kind == "video"
        and item.input_image_hash is not None
        and previous.input_image_hash == item.input_image_hash
    )
    clip_kind = visual_clip_kind(item, snapshot)
    composition = stage_composition(state)
    command = VisualPlayCommand(
        play_id=play_id,
        asset_kind=item.asset_kind,
        visual_id=item.id,
        visual_version=item.version,
        media_kind=media_kind,
        media_path=item.media_path,
        poster_path=item.poster_path,
        input_image_hash=item.input_image_hash,
        transition="natural" if natural else "fade",
        clip_kind=clip_kind,
        loop=media_kind == "image" or clip_kind != "once",
        presentation_epoch=presentation.revision,
        state_revision=revision,
        source=source,
        expires_at=expires_at,
        composition=composition,
        outfit=_frozen_outfit(snapshot),
        scene=_frozen_scene(snapshot),
    )
    pending = command.model_dump(mode="json")
    changed = (
        update(VisualPresentation.__table__)
        .where(
            VisualPresentation.user_id == user_id,
            VisualPresentation.state_revision == state.state_revision,
            VisualPresentation.presentation_epoch == state.presentation_epoch,
        )
        .values(
            state_revision=revision,
            presentation_epoch=presentation.revision,
            pending_command_json=pending,
            pending_static_json=None,
            updated_at=now,
        )
        .returning(VisualPresentation.user_id)
        .cte("queued_presentation")
    )
    table = VisualPlayback.__table__
    values = {
        "user_id": user_id,
        "visual_id": item.id,
        "visual_version": item.version,
        "version_id": version_id if version_id is not None else item.current_version_id,
        "play_id": play_id,
        "source_key": uuid4().hex,
        "presentation_epoch": presentation.revision,
        "state_revision": revision,
        "source": source,
        "status": "queued",
        "expires_at": expires_at,
        "snapshot_json": frozen.model_dump(mode="json"),
    }
    play = await db.scalar(
        insert(VisualPlayback)
        .from_select(
            list(values),
            select(*(literal(value, type_=table.c[key].type) for key, value in values.items())).select_from(changed),
            include_defaults=False,
        )
        .returning(VisualPlayback)
    )
    if play is None:
        raise VisualStateError("呈现状态已变化，请重新播放")
    for key, value in {
        "state_revision": revision,
        "presentation_epoch": presentation.revision,
        "pending_command_json": pending,
        "updated_at": now,
    }.items():
        set_committed_value(state, key, value)
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": revision})
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.visual.play",
        payload=client_play_command(command).model_dump(mode="json"),
    )
    return play, command


def _frozen_outfit(snapshot: VisualSnapshot):
    source = snapshot.source
    return source.outfit if source.kind in ("actor", "cinematic") else None


def _frozen_scene(snapshot: VisualSnapshot):
    source = snapshot.source
    return source.scene if source.kind in ("scene", "cinematic") else None


def emit_playback_receipt(
    db: AsyncSession,
    user_id: int,
    play_id: str,
    status: PlaybackStatus,
    receipt_kind: str | None,
) -> None:
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.visual.playback",
        payload={"play_id": play_id, "status": status, "receipt_kind": receipt_kind},
    )


def clear_pending_command(state: VisualPresentation, play_id: str) -> None:
    if not state.pending_command_json:
        return
    pending = VisualPlayCommand.model_validate(state.pending_command_json)
    if pending.play_id == play_id:
        state.pending_command_json = None


async def discard_job_draft(db: AsyncSession, user_id: int, job: VisualJob) -> None:
    """放弃制作草稿：回收不再被成品与其它工作项引用的文件。"""

    paths = asset_paths(job.checkpoint, user_id) | asset_paths(job.candidates_json, user_id)
    job.checkpoint = None
    job.candidates_json = None
    await db.flush()
    dropped = paths - await live_asset_paths(db, user_id)
    if dropped:
        await enqueue_asset_cleanup(db, user_id, dropped)
