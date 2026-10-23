"""影像库的行锁、状态读写、播放账本与成品发布；生成流程由应用层管理。"""

from datetime import timedelta
from uuid import uuid4

from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference
from infrastructure.assets.references import asset_paths
from infrastructure.text import utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import lock_user_row
from features.assets.lifecycle import enqueue_asset_cleanup
from features.companion.proactive_runtime import get_presentation_snapshot

from .asset_references import live_asset_paths
from .models import VisualItem, VisualJob, VisualPlayback, VisualPresentation
from .ports import read_material_links
from .schemas import (
    ActionClipKind,
    ComboSnapshot,
    PlaybackSource,
    PlaybackStatus,
    PresentedVisual,
    VisualAsset,
    VisualJobProgress,
    VisualMediaKind,
    VisualPlayCommand,
    parse_combo_snapshot,
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
    return parse_job_progress(job.progress_json)


def visual_snapshot(row: VisualItem | VisualJob | PresentedVisual) -> ComboSnapshot:
    return parse_combo_snapshot(row.snapshot_json)


def visual_clip_kind(snapshot: ComboSnapshot) -> ActionClipKind:
    return snapshot.action.clip_kind


def visual_plays_as_loop(item: VisualItem, snapshot: ComboSnapshot | None = None) -> bool:
    """图片是持续画面，视频按动作的播放方式决定是否持续循环。"""

    if item.media_kind == "image":
        return True
    return visual_clip_kind(snapshot or visual_snapshot(item)) == "loop"


def visual_is_short_action(item: VisualItem, snapshot: ComboSnapshot | None = None) -> bool:
    return item.media_kind == "video" and visual_clip_kind(snapshot or visual_snapshot(item)) == "once"


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
    frozen = PresentedVisual.model_validate_json(saved)
    if frozen.user_id != state.user_id:
        raise VisualStateError("呈现快照不属于当前账户")
    return frozen


async def visual_item(db: AsyncSession, user_id: int, visual_id: int, *, lock: bool = False) -> VisualItem:
    if lock:
        await lock_user_row(db, user_id)
    item = await db.scalar(
        select(VisualItem)
        .where(VisualItem.id == visual_id, VisualItem.user_id == user_id)
        .execution_options(populate_existing=True),
    )
    if item is None:
        raise VisualNotFoundError("找不到对应的成品")
    return item


async def visual_job_row(db: AsyncSession, user_id: int, job_id: int, *, lock: bool = False) -> VisualJob:
    if lock:
        await lock_user_row(db, user_id)
    job = await db.scalar(
        select(VisualJob)
        .where(VisualJob.id == job_id, VisualJob.user_id == user_id)
        .execution_options(populate_existing=True),
    )
    if job is None:
        raise VisualNotFoundError("找不到对应的制作工作项")
    return job


async def presentation_row(
    db: AsyncSession,
    user_id: int,
    *,
    create: bool = False,
    lock: bool = False,
) -> VisualPresentation | None:
    if create or lock:
        await lock_user_row(db, user_id)
    row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
    if row is not None and (create or lock) and not db.is_modified(row):
        await db.refresh(row)
    if row is None and create:
        row = VisualPresentation(user_id=user_id)
        db.add(row)
        await db.flush()
    return row


async def require_presentation_row(db: AsyncSession, user_id: int, *, lock: bool = False) -> VisualPresentation:
    """写入路径取账户呈现行，不存在时创建；返回的行必定可用。"""

    row = await presentation_row(db, user_id, create=True, lock=lock)
    if row is None:
        raise VisualStateError("呈现状态不可用，请稍后重试")
    return row


async def playback_row(db: AsyncSession, user_id: int, play_id: str, *, lock: bool = False) -> VisualPlayback:
    if lock:
        await lock_user_row(db, user_id)
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

    state = await require_presentation_row(db, user_id)
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.updated", payload={"version": state.state_revision})


def _media_hash(path: str, message: str) -> str:
    resolved = resolve_asset_reference(path)
    if resolved is None:
        raise VisualStateError(message)
    return compute_file_sha256(resolved[0])


async def _item_columns(user_id: int, snapshot: ComboSnapshot) -> dict[str, int | None]:
    return (await read_material_links(user_id, snapshot)).model_dump()


async def publish_image_result(
    db: AsyncSession,
    user_id: int,
    asset: VisualAsset,
    *,
    snapshot: ComboSnapshot,
) -> VisualItem:
    """图片成品入库；同账户同内容只保留一条图片记录，结果指针由调用方登记。"""

    if asset.media_kind != "image":
        raise VisualStateError("图片发布收到非图片成品")
    content_hash = _media_hash(asset.media_path, "生成的图片无法读取，请重新制作")
    existing = await db.scalar(
        select(VisualItem).where(
            VisualItem.user_id == user_id,
            VisualItem.media_kind == "image",
            VisualItem.content_hash == content_hash,
        ),
    )
    if existing is not None:
        return existing
    item = VisualItem(
        user_id=user_id,
        media_kind="image",
        media_path=asset.media_path,
        width=asset.width,
        height=asset.height,
        content_hash=content_hash,
        description=asset.visual_description,
        outfit_description=asset.outfit_description,
        snapshot_json=snapshot.model_dump_json(),
        **await _item_columns(user_id, snapshot),
    )
    db.add(item)
    await db.flush()
    return item


async def publish_video_result(
    db: AsyncSession,
    user_id: int,
    asset: VisualAsset,
    *,
    snapshot: ComboSnapshot,
    input_image: VisualItem | None,
) -> VisualItem:
    """视频成品入库：封面只写 poster_path，真实输入图冻结在本行。"""

    if asset.media_kind != "video":
        raise VisualStateError("视频发布收到非视频成品")
    if resolve_asset_reference(asset.media_path) is None or resolve_asset_reference(asset.poster_path or "") is None:
        raise VisualStateError("视频或封面缺失，请重新制作")
    input_path = asset.input_image_path or (input_image.media_path if input_image is not None else "")
    item = VisualItem(
        user_id=user_id,
        media_kind="video",
        media_path=asset.media_path,
        poster_path=asset.poster_path or None,
        width=asset.width,
        height=asset.height,
        duration_ms=asset.duration_ms,
        content_hash=compute_file_sha256(resolve_asset_reference(asset.media_path)[0]),
        description=asset.visual_description,
        outfit_description=asset.outfit_description,
        snapshot_json=snapshot.model_dump_json(),
        input_image_id=input_image.id if input_image is not None else None,
        input_image_path=input_path or None,
        input_image_hash=_media_hash(input_path, "视频的起始画面无法读取") if input_path else None,
        **await _item_columns(user_id, snapshot),
    )
    db.add(item)
    await db.flush()
    return item


async def replace_visual_result(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    target: VisualItem,
    asset: VisualAsset,
    *,
    snapshot: ComboSnapshot,
    input_image: VisualItem | None = None,
) -> VisualItem:
    """重做成功后同 ID 替换成品；先核对目标与版本，旧文件交统一回收。"""

    if job.target_visual_id != target.id:
        raise VisualStateError("重做目标已经变化，本次结果未替换")
    if job.expected_version is not None and target.version != job.expected_version:
        raise VisualStateError("影像已按新版本更新，本次结果未替换")
    if target.media_kind != asset.media_kind:
        raise VisualStateError("重做结果与目标的媒体类型不一致，本次结果未替换")
    dropped = asset_paths(
        [target.media_path, target.poster_path, target.input_image_path],
        user_id,
    )
    content_hash = _media_hash(asset.media_path, "生成的成品无法读取，请重新制作")
    if asset.media_kind == "image":
        await db.execute(
            update(VisualItem)
            .where(
                VisualItem.user_id == user_id,
                VisualItem.input_image_id == target.id,
            )
            .values(input_image_id=None),
        )
        conflict = await db.scalar(
            select(VisualItem.id).where(
                VisualItem.user_id == user_id,
                VisualItem.media_kind == "image",
                VisualItem.content_hash == content_hash,
                VisualItem.id != target.id,
            ),
        )
        if conflict is not None:
            raise VisualStateError("重做的图片与库中已有影像完全相同，本次结果未替换")
        target.poster_path = None
        target.duration_ms = None
        target.input_image_id = None
        target.input_image_path = None
        target.input_image_hash = None
    else:
        if resolve_asset_reference(asset.poster_path or "") is None:
            raise VisualStateError("视频封面缺失，请重新制作")
        input_path = asset.input_image_path or (input_image.media_path if input_image is not None else "")
        target.poster_path = asset.poster_path or None
        target.duration_ms = asset.duration_ms
        target.input_image_id = input_image.id if input_image is not None else target.input_image_id
        target.input_image_path = input_path or target.input_image_path
        target.input_image_hash = (
            _media_hash(input_path, "视频的起始画面无法读取") if input_path else target.input_image_hash
        )
    target.media_path = asset.media_path
    target.width = asset.width
    target.height = asset.height
    target.content_hash = content_hash
    target.description = asset.visual_description
    target.outfit_description = asset.outfit_description
    target.snapshot_json = snapshot.model_dump_json()
    target.status = "ready"
    target.version += 1
    for field, value in (await _item_columns(user_id, snapshot)).items():
        setattr(target, field, value)
    job.result_visual_id = target.id
    await db.flush()
    dropped -= await live_asset_paths(db, user_id)
    if dropped:
        await enqueue_asset_cleanup(db, user_id, dropped)
    return target


async def dispatch_play_command(
    db: AsyncSession,
    user_id: int,
    item: VisualItem,
    *,
    source: PlaybackSource,
    snapshot: ComboSnapshot | None = None,
) -> tuple[VisualPlayback, VisualPlayCommand]:
    """登记待执行指令；实际呈现指针只在已认领的开播回执中推进。"""

    snapshot = snapshot or visual_snapshot(item)
    presentation = get_presentation_snapshot(user_id)
    if presentation is None:
        raise VisualStateError("桌面客户端当前不可用，无法播放")
    state = await touch_presentation(db, user_id)
    state.presentation_epoch = presentation.revision
    now = utc_now()
    media_kind: VisualMediaKind = "image" if item.media_kind == "image" else "video"
    play = VisualPlayback(
        user_id=user_id,
        visual_id=item.id,
        visual_version=item.version,
        play_id=uuid4().hex,
        presentation_epoch=state.presentation_epoch,
        state_revision=state.state_revision,
        source=source,
        status="queued",
        expires_at=now + timedelta(seconds=DEFERRED_PLAY_INTENT_TTL_SECONDS),
    )
    db.add(play)
    frozen = PresentedVisual.model_validate(item).model_copy(update={"play_id": play.play_id})
    play.snapshot_json = frozen.model_dump_json()
    previous = presented_item(state, loop=True)
    natural = (
        previous is not None
        and previous.media_kind == "video"
        and item.media_kind == "video"
        and item.input_image_hash is not None
        and previous.input_image_hash == item.input_image_hash
    )
    command = VisualPlayCommand(
        play_id=play.play_id,
        visual_id=item.id,
        visual_version=item.version,
        media_kind=media_kind,
        media_path=item.media_path,
        poster_path=item.poster_path,
        input_image_hash=item.input_image_hash,
        transition="natural" if natural else "fade",
        clip_kind=visual_clip_kind(snapshot),
        loop=visual_plays_as_loop(item, snapshot),
        presentation_epoch=state.presentation_epoch,
        state_revision=state.state_revision,
        source=source,
        expires_at=play.expires_at,
        outfit=snapshot.outfit,
        scene=snapshot.scene,
    )
    state.pending_command_json = command.model_dump_json()
    emit_ws_event(db, user_id=user_id, event_type="companion.visual.play", payload=command.model_dump(mode="json"))
    await db.flush()
    return play, command


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
    pending = VisualPlayCommand.model_validate_json(state.pending_command_json)
    if pending.play_id == play_id:
        state.pending_command_json = None


async def discard_job_draft(db: AsyncSession, user_id: int, job: VisualJob) -> None:
    """放弃制作草稿：回收不再被成品与其它工作项引用的文件。"""

    paths = asset_paths(job.progress_json, user_id) | asset_paths(job.candidates_json, user_id)
    job.progress_json = None
    job.candidates_json = None
    await db.flush()
    dropped = paths - await live_asset_paths(db, user_id)
    if dropped:
        await enqueue_asset_cleanup(db, user_id, dropped)
