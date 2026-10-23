"""影像库的只读查询与对外视图装配；签名 URL 只在响应出口生成。"""

from infrastructure.assets.asset_store import client_asset_url, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record

from .models import VisualItem, VisualJob, VisualPresentation
from .repository import VisualNotFoundError, presented_item, visual_playable, visual_snapshot
from .schemas import (
    ActualPresentationSnapshot,
    ComboSnapshot,
    PresentedVisual,
    VisualDetailView,
    VisualItemView,
    VisualJobListResponse,
    VisualJobState,
    VisualJobView,
    VisualListResponse,
    VisualMediaKind,
    VisualPlayCommand,
    VisualPlayOption,
    VisualPresentationView,
    parse_job_progress,
)


def _media_kind(value: str) -> VisualMediaKind:
    return "image" if value == "image" else "video"


def item_view(item: VisualItem | PresentedVisual, snapshot: ComboSnapshot | None = None) -> VisualItemView:
    snapshot = snapshot or visual_snapshot(item)
    return VisualItemView(
        id=item.id,
        media_kind=_media_kind(item.media_kind),
        clip_kind=snapshot.action.clip_kind,
        title=item.title,
        status=item.status,
        media_url=client_asset_url(item.media_path),
        poster_url=client_asset_url(item.poster_path) if item.poster_path else None,
        width=item.width,
        height=item.height,
        duration_ms=item.duration_ms,
        version=item.version,
        description=item.description,
        outfit_id=item.outfit_id,
        outfit_name=snapshot.outfit.name,
        scene_id=item.scene_id,
        scene_title=snapshot.scene.name,
        action_id=item.action_id,
        action_name=snapshot.action.name,
        input_image_id=item.input_image_id,
        input_image_hash=item.input_image_hash,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def job_view(job: VisualJob) -> VisualJobView:
    progress = parse_job_progress(job.progress_json)
    return VisualJobView(
        id=job.id,
        purpose=job.purpose,
        target_kind=_media_kind(job.target_kind),
        target_visual_id=job.target_visual_id,
        expected_version=job.expected_version,
        state=job.state,  # type: ignore[arg-type]
        stage=job.stage,
        feedback=job.feedback,
        video_feedback=job.video_feedback,
        repair_count=job.repair_count,
        repair_limit=job.repair_limit,
        error=job.error,
        result_visual_id=job.result_visual_id,
        result_image_id=job.result_image_id,
        video_queue=progress.video_queue if progress is not None else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def list_visuals(
    user_id: int,
    *,
    query: str | None = None,
    media_kind: VisualMediaKind | None = None,
    outfit_id: int | None = None,
    scene_id: int | None = None,
    action_id: int | None = None,
    source: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> VisualListResponse:
    """成品分页；制作工作项由 list_jobs 单独读取。"""

    async with SESSION_LOCAL() as db:
        filters = [VisualItem.user_id == user_id]
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            filters.append(or_(VisualItem.title.ilike(pattern), VisualItem.description.ilike(pattern)))
        if media_kind is not None:
            filters.append(VisualItem.media_kind == media_kind)
        if outfit_id is not None:
            filters.append(VisualItem.outfit_id == outfit_id)
        if scene_id is not None:
            filters.append(VisualItem.scene_id == scene_id)
        if action_id is not None:
            filters.append(VisualItem.action_id == action_id)
        if source and source.strip():
            pattern = f"%{source.strip()}%"
            snapshot = cast(VisualItem.snapshot_json, JSONB)
            filters.append(
                or_(
                    snapshot["outfit"]["name"].astext.ilike(pattern),
                    snapshot["scene"]["name"].astext.ilike(pattern),
                    snapshot["action"]["name"].astext.ilike(pattern),
                ),
            )
        total = await db.scalar(select(func.count()).select_from(VisualItem).where(*filters)) or 0
        items = list(
            await db.scalars(
                select(VisualItem).where(*filters).order_by(VisualItem.id.desc()).offset(offset).limit(limit),
            ),
        )
        return VisualListResponse(
            visuals=[item_view(item) for item in items],
            total=total,
            offset=offset,
            limit=limit,
        )


async def visual_detail(user_id: int, visual_id: int) -> VisualDetailView:
    async with SESSION_LOCAL() as db:
        item = await db.scalar(
            select(VisualItem).where(VisualItem.id == visual_id, VisualItem.user_id == user_id),
        )
        if item is None:
            raise VisualNotFoundError("找不到对应的成品")
        jobs = list(
            await db.scalars(
                select(VisualJob)
                .where(
                    VisualJob.user_id == user_id,
                    or_(
                        VisualJob.target_visual_id == visual_id,
                        VisualJob.result_visual_id == visual_id,
                        VisualJob.result_image_id == visual_id,
                    ),
                )
                .order_by(VisualJob.id.desc()),
            ),
        )
        snapshot = visual_snapshot(item)
        return VisualDetailView(
            **item_view(item, snapshot).model_dump(),
            snapshot=snapshot,
            replaced_at_version=item.version - 1 if item.version > 1 else None,
            jobs=[job_view(job) for job in jobs],
        )


async def list_jobs(
    user_id: int,
    *,
    states: list[VisualJobState] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> VisualJobListResponse:
    async with SESSION_LOCAL() as db:
        filters = [VisualJob.user_id == user_id]
        if states:
            filters.append(VisualJob.state.in_(states))
        total = await db.scalar(select(func.count()).select_from(VisualJob).where(*filters)) or 0
        jobs = list(
            await db.scalars(
                select(VisualJob).where(*filters).order_by(VisualJob.id.desc()).offset(offset).limit(limit),
            ),
        )
        return VisualJobListResponse(jobs=[job_view(job) for job in jobs], total=total, offset=offset, limit=limit)


async def presentation_state(user_id: int) -> VisualPresentationView:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if row is None:
            return VisualPresentationView()
        current = await presented_item(db, row)
        loop = await presented_item(db, row, loop=True)
        pending = _pending_command(row)
        return VisualPresentationView(
            current_visual=item_view(current) if current is not None and current.user_id == user_id else None,
            loop_visual=item_view(loop) if loop is not None and loop.user_id == user_id else None,
            pinned=row.pinned,
            autonomous_enabled=row.autonomous_enabled,
            target_width=row.target_width,
            target_height=row.target_height,
            presentation_epoch=row.presentation_epoch,
            state_revision=row.state_revision,
            pending_play=pending,
        )


def _pending_command(row: VisualPresentation) -> VisualPlayCommand | None:
    """待播指令过期后不再提供；换号与模式切换在认领时重新校验。"""

    if not row.pending_command_json:
        return None
    command = VisualPlayCommand.model_validate_json(row.pending_command_json)
    return None if ensure_utc(command.expires_at) <= utc_now() else command


async def actual_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """实际呈现快照：冻结造型与环境资料加成品描述；素材缺失的字段为空。"""

    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        item = await presented_item(db, row) if row else None
        if item is None or item.user_id != user_id:
            return ActualPresentationSnapshot()
        snapshot = visual_snapshot(item)
        presence = get_presentation_snapshot(user_id)
        desktop = presence is not None and presence.mode == "desktop"
        return ActualPresentationSnapshot(
            visual_id=item.id,
            media_kind=_media_kind(item.media_kind),
            visual_version=item.version,
            visual_description=item.description if desktop else "",
            visual_path=_existing(
                item.media_path if item.media_kind == "image" else item.input_image_path or item.poster_path,
            )
            if desktop
            else None,
            outfit_path=_existing(snapshot.outfit.path),
            outfit_hash=snapshot.outfit.content_hash if _existing(snapshot.outfit.path) else None,
            outfit_description=item.outfit_description if desktop else snapshot.outfit.description,
            scene_path=_existing(snapshot.scene.path),
            scene_hash=snapshot.scene.content_hash if _existing(snapshot.scene.path) else None,
            scene_description=snapshot.scene.description,
            identity_path=_existing(snapshot.identity_path),
            identity_hash=snapshot.identity_hash if _existing(snapshot.identity_path) else None,
            identity_reference_role=snapshot.identity_reference_role,
            ready=presence is not None and get_user_proactive_record(user_id).available and visual_playable(item),
        )


def _existing(reference: str | None) -> str | None:
    resolved = resolve_asset_reference(reference)
    return reference if resolved is not None and resolved[0].is_file() else None


async def visual_play_catalog(user_id: int, trigger: str) -> list[VisualPlayOption]:
    """按动作触发条件过滤可播影像；播放方式与条件来自组合快照的动作引用。"""

    async with SESSION_LOCAL() as db:
        items = list(
            await db.scalars(
                select(VisualItem).where(VisualItem.user_id == user_id).order_by(VisualItem.id.desc()),
            ),
        )
    options: list[VisualPlayOption] = []
    for item in items:
        if not visual_playable(item):
            continue
        snapshot = visual_snapshot(item)
        if trigger not in snapshot.action.allowed_triggers:
            continue
        options.append(
            VisualPlayOption(
                visual_id=item.id,
                name=snapshot.action.name,
                media_kind=_media_kind(item.media_kind),
                clip_kind=snapshot.action.clip_kind,
                duration_seconds=snapshot.action.duration_seconds,
                use_when=list(snapshot.action.use_when),
                avoid_when=list(snapshot.action.avoid_when),
            ),
        )
    return options
