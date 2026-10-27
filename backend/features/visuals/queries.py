"""影像库的只读查询与对外视图装配；签名 URL 只在响应出口生成。"""

from datetime import datetime, timedelta

from infrastructure.assets.asset_store import client_asset_url, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import func, or_, select

from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.jobs.service import requested_job_id

from .models import VisualItem, VisualJob, VisualPresentation, VisualVersion
from .repository import (
    VisualNotFoundError,
    presentation_outfit,
    presented_item,
    stage_composition,
    visual_playable,
    visual_snapshot,
)
from .schemas import (
    ActionMediaEntry,
    ActionMediaListResponse,
    ActorClipSource,
    ActualPresentationSnapshot,
    ComboMaterialRef,
    PresentedVisual,
    StageIntent,
    StagePresentation,
    VisualAssetKind,
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
    VisualSnapshot,
    parse_job_progress,
)

_ASSET_KINDS: tuple[str, ...] = ("actor", "scene", "cinematic")


async def creation_request(user_id: int, request_id: str) -> tuple[VisualJobView, VisualSnapshot] | None:
    async with SESSION_LOCAL() as db:
        job_id = await requested_job_id(db, user_id, request_id)
        job = (
            await db.scalar(select(VisualJob).where(VisualJob.id == job_id, VisualJob.user_id == user_id))
            if job_id is not None
            else None
        )
        return (job_view(job), visual_snapshot(job)) if job is not None else None


async def current_outfit_reference(user_id: int) -> ComboMaterialRef | None:
    """只读已经采用的造型；待播目标与衣柜排序不能代替实际采用。"""

    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if row is None:
            return None
        outfit = presentation_outfit(row)
        if outfit is not None or row.current_static_json:
            return outfit
        composition = stage_composition(row)
        if composition is None or composition.mode != "layered" or composition.actor is None:
            return None
        version = await db.scalar(
            select(VisualVersion).where(
                VisualVersion.user_id == user_id,
                VisualVersion.visual_id == composition.actor.visual_id,
                VisualVersion.number == composition.actor.version,
            )
        )
        source = VisualSnapshot.model_validate(version.snapshot_json).source if version is not None else None
        return source.outfit if isinstance(source, ActorClipSource) else None


async def reusable_actor_clip(user_id: int, source: ActorClipSource) -> bool:
    """基础准备只复用身份、造型、动作版本相符且文件完整的透明成品。"""

    async with SESSION_LOCAL() as db:
        rows = await db.execute(
            select(VisualVersion.media_path, VisualVersion.snapshot_json, VisualVersion.body_json)
            .join(VisualItem, VisualItem.current_version_id == VisualVersion.id)
            .where(
                VisualItem.user_id == user_id,
                VisualItem.asset_kind == "actor",
                VisualItem.media_kind == "video",
                VisualItem.status == "ready",
                VisualItem.outfit_id == source.outfit.material_id,
                VisualItem.action_id == source.action.action_id,
            ),
        )
        for path, snapshot_json, body in rows:
            frozen = VisualSnapshot.model_validate(snapshot_json).source
            if not isinstance(frozen, ActorClipSource) or not body:
                continue
            if (
                frozen.identity_hash == source.identity_hash
                and frozen.portrait_hash == source.portrait_hash
                and frozen.outfit.content_hash == source.outfit.content_hash
                and frozen.action == source.action
                and all(
                    _existing(value)
                    for value in (
                        path,
                        body.get("mask_path"),
                        body.get("first_frame_path"),
                        body.get("last_frame_path"),
                    )
                )
            ):
                return True
    return False


def _media_kind(value: str) -> VisualMediaKind:
    return "image" if value == "image" else "video"


def _asset_kind(value: str) -> VisualAssetKind:
    return value if value in _ASSET_KINDS else "cinematic"


def item_view(item: VisualItem | PresentedVisual, snapshot: VisualSnapshot | None = None) -> VisualItemView:
    snapshot = snapshot or visual_snapshot(item)
    source = snapshot.source
    outfit = source.outfit if source.kind in ("actor", "cinematic") else None
    scene = source.scene if source.kind in ("scene", "cinematic") else None
    action = source.action if source.kind in ("actor", "cinematic") else None
    return VisualItemView(
        id=item.id,
        media_kind=_media_kind(item.media_kind),
        asset_kind=_asset_kind(item.asset_kind),
        clip_kind=item.clip_kind,
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
        outfit_name=outfit.name if outfit else "",
        scene_id=item.scene_id,
        scene_title=scene.title if scene else "",
        action_id=item.action_id,
        action_name=action.name if action else None,
        input_image_id=item.input_image_id,
        input_image_hash=item.input_image_hash,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _job_action_name(snapshot: dict) -> str:
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    if isinstance(source, dict) and source.get("kind") in ("actor", "cinematic"):
        action = source.get("action")
        name = action.get("name") if isinstance(action, dict) else None
        return name if isinstance(name, str) else ""
    return ""


def job_view(job: VisualJob) -> VisualJobView:
    progress = parse_job_progress(job.checkpoint)
    return VisualJobView(
        id=job.id,
        purpose=job.purpose,
        target_kind=_media_kind(job.target_kind),
        asset_kind=_asset_kind(job.asset_kind),
        action_name=_job_action_name(job.snapshot_json),
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
    asset_kind: VisualAssetKind | None = None,
    outfit_id: int | None = None,
    scene_id: int | None = None,
    action_id: int | None = None,
    source: str | None = None,
    cursor: int | None = None,
    include_total: bool = False,
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
        if asset_kind is not None:
            filters.append(VisualItem.asset_kind == asset_kind)
        if outfit_id is not None:
            filters.append(VisualItem.outfit_id == outfit_id)
        if scene_id is not None:
            filters.append(VisualItem.scene_id == scene_id)
        if action_id is not None:
            filters.append(VisualItem.action_id == action_id)
        if source and source.strip():
            pattern = f"%{source.strip()}%"
            snapshot = VisualVersion.snapshot_json
            filters.append(
                or_(
                    snapshot["source"]["outfit"]["name"].astext.ilike(pattern),
                    snapshot["source"]["scene"]["title"].astext.ilike(pattern),
                    snapshot["source"]["action"]["name"].astext.ilike(pattern),
                ),
            )
        total = (
            (
                await db.scalar(
                    select(func.count())
                    .select_from(VisualItem)
                    .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
                    .where(*filters)
                )
                or 0
            )
            if include_total
            else None
        )
        if cursor is not None:
            filters.append(VisualItem.id < cursor)
        fields = (
            VisualItem.id,
            VisualItem.media_kind,
            VisualItem.asset_kind,
            VisualItem.clip_kind,
            VisualItem.title,
            VisualItem.status,
            VisualItem.description,
            VisualItem.outfit_id,
            VisualItem.scene_id,
            VisualItem.action_id,
            VisualItem.created_at,
            VisualItem.updated_at,
            VisualVersion.number.label("version"),
            VisualVersion.media_path,
            VisualVersion.poster_path,
            VisualVersion.width,
            VisualVersion.height,
            VisualVersion.duration_ms,
            VisualVersion.input_image_id,
            VisualVersion.input_image_hash,
            VisualVersion.snapshot_json["source"]["outfit"]["name"].astext.label("outfit_name"),
            VisualVersion.snapshot_json["source"]["scene"]["title"].astext.label("scene_title"),
            VisualVersion.snapshot_json["source"]["action"]["name"].astext.label("action_name"),
        )
        rows = list(
            (
                await db.execute(
                    select(*fields)
                    .select_from(VisualItem)
                    .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
                    .where(*filters)
                    .order_by(VisualItem.id.desc())
                    .limit(limit + 1)
                )
            ).mappings()
        )
        views = []
        for row in rows[:limit]:
            values = dict(row)
            values["media_url"] = client_asset_url(values.pop("media_path"))
            poster = values.pop("poster_path")
            values["poster_url"] = client_asset_url(poster) if poster else None
            views.append(VisualItemView.model_validate(values))
        return VisualListResponse(
            visuals=views,
            total=total,
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
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
                    or_(VisualJob.finished_at.is_(None), VisualJob.finished_at >= utc_now() - timedelta(days=90)),
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
    cursor: int | None = None,
    include_total: bool = False,
    limit: int = 50,
) -> VisualJobListResponse:
    async with SESSION_LOCAL() as db:
        filters = [
            VisualJob.user_id == user_id,
            or_(VisualJob.finished_at.is_(None), VisualJob.finished_at >= utc_now() - timedelta(days=90)),
        ]
        if states:
            filters.append(VisualJob.state.in_(states))
        total = (
            (await db.scalar(select(func.count()).select_from(VisualJob).where(*filters)) or 0)
            if include_total
            else None
        )
        if cursor is not None:
            filters.append(VisualJob.id < cursor)
        fields = (
            VisualJob.id,
            VisualJob.purpose,
            VisualJob.target_kind,
            VisualJob.asset_kind,
            func.coalesce(VisualJob.snapshot_json["source"]["action"]["name"].astext, "").label("action_name"),
            VisualJob.target_visual_id,
            VisualJob.expected_version,
            VisualJob.state,
            VisualJob.stage,
            VisualJob.feedback,
            VisualJob.video_feedback,
            VisualJob.repair_count,
            VisualJob.repair_limit,
            VisualJob.error,
            VisualJob.result_visual_id,
            VisualJob.result_image_id,
            VisualJob.created_at,
            VisualJob.updated_at,
            VisualJob.checkpoint["video_queue"].label("video_queue"),
        )
        rows = list(
            (
                await db.execute(
                    select(*fields)
                    .select_from(VisualJob)
                    .where(*filters)
                    .order_by(VisualJob.id.desc())
                    .limit(limit + 1)
                )
            ).mappings()
        )
        return VisualJobListResponse(
            jobs=[VisualJobView.model_validate(dict(row)) for row in rows[:limit]],
            total=total,
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            limit=limit,
        )


async def recent_autonomous_failure_action_ids(user_id: int, *, since: datetime) -> set[int]:
    """返回退避期内自主制作失败过的动作，避免决策反复提交同一动作。"""

    async with SESSION_LOCAL() as db:
        rows = await db.scalars(
            select(VisualJob.action_id).where(
                VisualJob.user_id == user_id,
                VisualJob.purpose == "create",
                VisualJob.state == "failed",
                VisualJob.finished_at >= since,
                VisualJob.action_id.is_not(None),
                VisualJob.checkpoint["source"].astext == "autonomous",
            )
        )
        return {action_id for action_id in rows if action_id is not None}


async def presentation_state(user_id: int) -> VisualPresentationView:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if row is None:
            return VisualPresentationView()
        current = presented_item(row)
        loop = presented_item(row, loop=True)
        pending = _pending_command(row)
        return VisualPresentationView(
            composition=stage_composition(row),
            intent=StageIntent.model_validate(row.intent_json) if row.intent_json else None,
            current_visual=item_view(current) if current is not None and current.user_id == user_id else None,
            loop_visual=item_view(loop) if loop is not None and loop.user_id == user_id else None,
            autonomous_enabled=row.autonomous_enabled,
            target_width=row.target_width,
            target_height=row.target_height,
            presentation_epoch=row.presentation_epoch,
            state_revision=row.state_revision,
            pending_play=pending,
            static_presentation=row.pending_static_json or row.current_static_json,
        )


def _pending_command(row: VisualPresentation) -> VisualPlayCommand | None:
    """待播指令过期后不再提供；换号与模式切换在认领时重新校验。"""

    if not row.pending_command_json:
        return None
    command = VisualPlayCommand.model_validate(row.pending_command_json)
    return None if ensure_utc(command.expires_at) <= utc_now() else command


async def _presentation_snapshot(user_id: int, *, for_prompt: bool) -> ActualPresentationSnapshot:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if row and row.current_static_json:
            static = StagePresentation.model_validate(row.current_static_json)
            if static.outfit is None or static.scene is None:
                return ActualPresentationSnapshot()
            presence = get_presentation_snapshot(user_id)
            outfit_path = _existing(static.outfit.path)
            scene_path = _existing(static.scene.path)
            return ActualPresentationSnapshot(
                outfit_path=outfit_path,
                outfit_hash=static.outfit.content_hash if outfit_path else None,
                outfit_description=static.outfit.description,
                scene_path=scene_path,
                scene_hash=static.scene.content_hash if scene_path else None,
                scene_description=static.scene.description,
                identity_path=_existing(static.identity_path),
                identity_hash=static.identity_hash,
                ready=static.receipt_kind is not None
                and presence is not None
                and static.presentation_epoch == presence.revision
                and static.receipt_kind == ("window_static" if presence.mode == "window" else "desktop_static")
                and outfit_path is not None
                and scene_path is not None
                and get_user_proactive_record(user_id).available,
            )
        item = presented_item(row) if row else None
        if item is None or item.user_id != user_id:
            return ActualPresentationSnapshot()
        snapshot = visual_snapshot(item)
        presence = get_presentation_snapshot(user_id)
        show_full_visual = for_prompt or (presence is not None and presence.mode == "desktop")
        visual_path = _existing(
            item.media_path if item.media_kind == "image" else item.input_image_path or item.poster_path,
        )
        source = snapshot.source
        outfit = source.outfit if source.kind in ("actor", "cinematic") else None
        scene = source.scene if source.kind in ("scene", "cinematic") else None
        outfit_path = _existing(outfit.path) if outfit else None
        scene_path = _existing(scene.path) if scene else None
        identity_path = _existing(source.identity_path) if source.kind in ("actor", "cinematic") else None
        if for_prompt:
            outfit_description = (item.outfit_description or outfit.description if outfit else "") or ""
        elif show_full_visual:
            outfit_description = item.outfit_description
        else:
            outfit_description = outfit.description if outfit else ""
        return ActualPresentationSnapshot(
            visual_id=item.id,
            media_kind=_media_kind(item.media_kind),
            asset_kind=_asset_kind(item.asset_kind),
            visual_version=item.version,
            visual_description=item.description if show_full_visual else "",
            visual_path=visual_path if show_full_visual else None,
            outfit_path=outfit_path,
            outfit_hash=outfit.content_hash if outfit and outfit_path else None,
            outfit_description=outfit_description,
            scene_path=scene_path,
            scene_hash=scene.content_hash if scene and scene_path else None,
            scene_description=scene.description if scene else "",
            identity_path=identity_path,
            identity_hash=source.identity_hash if identity_path and source.kind in ("actor", "cinematic") else None,
            identity_reference_role=source.identity_reference_role
            if source.kind in ("actor", "cinematic")
            else "portrait",
            ready=presence is not None and get_user_proactive_record(user_id).available and visual_playable(item),
        )


async def actual_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """实际呈现快照：冻结造型与环境资料加成品描述；素材缺失的字段为空。"""

    return await _presentation_snapshot(user_id, for_prompt=False)


async def prompt_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """供伙伴判断和媒体策划使用的模式无关快照。"""

    return await _presentation_snapshot(user_id, for_prompt=True)


def _existing(reference: str | None) -> str | None:
    resolved = resolve_asset_reference(reference)
    return reference if resolved is not None and resolved[0].is_file() else None


async def visual_play_catalog(user_id: int, trigger: str) -> list[VisualPlayOption]:
    """按动作触发条件过滤可播影像；播放方式与条件来自冻结输入的动作引用。"""

    async with SESSION_LOCAL() as db:
        rows = await db.execute(
            select(
                VisualItem.id,
                VisualItem.media_kind,
                VisualItem.asset_kind,
                VisualItem.clip_kind,
                VisualVersion.media_path,
                VisualVersion.description,
                VisualVersion.outfit_description,
                VisualVersion.snapshot_json["source"]["scene"]["description"].astext.label("scene_description"),
                VisualVersion.snapshot_json["source"].label("source"),
            )
            .select_from(VisualItem)
            .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
            .where(
                VisualItem.user_id == user_id,
                VisualItem.status == "ready",
                VisualItem.allowed_triggers.contains([trigger]),
            )
            .order_by(VisualItem.id.desc())
        )
        options = []
        for row in rows:
            if resolve_asset_reference(row.media_path) is None:
                continue
            source = row.source
            action = source.get("action") if source.get("kind") in ("actor", "cinematic") else None
            action = action or {}
            options.append(
                VisualPlayOption(
                    visual_id=row.id,
                    name=action.get("name", ""),
                    media_kind=row.media_kind,
                    asset_kind=_asset_kind(row.asset_kind),
                    clip_kind=row.clip_kind,
                    duration_seconds=action.get("duration_seconds"),
                    description=row.description,
                    outfit_description=row.outfit_description,
                    scene_description=row.scene_description or "",
                    use_when=action.get("use_when", []),
                    avoid_when=action.get("avoid_when", []),
                )
            )
        return options


async def action_media_implementations(
    user_id: int,
    action_id: int,
    *,
    cursor: int | None = None,
    limit: int = 30,
) -> ActionMediaListResponse:
    """动作详情的媒体实现清单：该动作已制作的可播成品投影。"""

    async with SESSION_LOCAL() as db:
        filters = [
            VisualItem.user_id == user_id,
            VisualItem.action_id == action_id,
            VisualItem.status == "ready",
        ]
        if cursor is not None:
            filters.append(VisualItem.id < cursor)
        fields = (
            VisualItem.id,
            VisualItem.media_kind,
            VisualItem.asset_kind,
            VisualItem.clip_kind,
            VisualItem.title,
            VisualItem.outfit_id,
            VisualItem.updated_at,
            VisualVersion.number.label("version"),
            VisualVersion.snapshot_json["source"]["outfit"]["name"].astext.label("outfit_name"),
        )
        rows = list(
            (
                await db.execute(
                    select(*fields)
                    .select_from(VisualItem)
                    .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
                    .where(*filters)
                    .order_by(VisualItem.id.desc())
                    .limit(limit + 1)
                )
            ).mappings()
        )
        return ActionMediaListResponse(
            media=[ActionMediaEntry.model_validate(dict(row)) for row in rows[:limit]],
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            limit=limit,
        )
