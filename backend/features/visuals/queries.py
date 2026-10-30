"""影像库的只读查询与对外视图装配；签名 URL 只在响应出口生成。"""

from datetime import datetime, timedelta
from typing import Protocol

from infrastructure.assets.asset_store import client_asset_url, resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.action_library.queries import action_by_preset_key, action_preset_keys
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.jobs.service import requested_job_id

from .client_assets import client_body
from .eligibility import actor_body_ready, automatic_media_ready, existing_asset
from .geometry import visual_aspect_matches
from .import_jobs import import_job_view
from .models import VisualImportJob, VisualItem, VisualJob, VisualPresentation, VisualVersion
from .repository import (
    VisualNotFoundError,
    presentation_outfit,
    presented_item,
    stage_composition,
    version_view,
    visual_clip_kind,
    visual_is_short_action,
    visual_playable,
    visual_snapshot,
)
from .schemas import (
    ActionMaterialRef,
    ActionMediaEntry,
    ActionMediaListResponse,
    ActorClipBody,
    ActorClipSource,
    ActualPresentationSnapshot,
    AmbientCatalogEntry,
    AnalyzingVisualSource,
    ComboMaterialRef,
    PresentedVisual,
    SceneMediaRef,
    StageComposition,
    StageCompositionMedia,
    StageIntent,
    StageLayerMedia,
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
    VisualSource,
    parse_job_progress,
)


def _source_matches_stage(source: dict, *, outfit: ComboMaterialRef | None, identity_hash: str | None) -> bool:
    """只让当前身份/造型的伙伴成品进入舞台清单；环境成品不绑定伙伴。"""

    if not source.get("presentation_approved", True):
        return False
    if source.get("kind") == "scene" or (source.get("kind") == "analysis" and source.get("asset_kind") == "scene"):
        return True
    return bool(
        outfit is not None
        and identity_hash
        and source.get("identity_hash") == identity_hash
        and (source.get("outfit") or {}).get("content_hash") == outfit.content_hash
    )


def _frozen_auto_selectable(source: VisualSource) -> bool | None:
    """冻结输入里的自动可播结论；环境媒体与未知语义为 None。"""

    if source.kind == "analysis":
        return bool(
            source.presentation_approved and (source.media_semantics is None or source.media_semantics.auto_selectable)
        )
    if source.kind not in ("actor", "cinematic"):
        return None
    semantics = source.media_semantics
    return semantics.auto_selectable if semantics is not None else None


def _fits_target_area(
    width: int,
    height: int,
    *,
    asset_kind: str,
    target_width: int | None,
    target_height: int | None,
) -> bool:
    """过滤明显不适合目标画幅的完整场景；透明主体与环境成品不按此收窄。"""

    if asset_kind != "cinematic" or not target_width or not target_height:
        return True
    return visual_aspect_matches(width, height, target_width, target_height)


class StageCatalogRow(Protocol):
    """舞台目录查询共用的准入字段。"""

    asset_kind: str
    clip_kind: str | None
    width: int | None
    height: int | None
    body_json: dict | None

    @property
    def source(self) -> dict | None: ...


def _stage_ready(
    row: StageCatalogRow,
    *,
    outfit: ComboMaterialRef | None,
    identity_hash: str | None,
    target_width: int | None = None,
    target_height: int | None = None,
) -> bool:
    """舞台目录共同准入：身份造型一致、画幅可用；actor 另要求合成数据齐全。"""

    source = row.source or {}
    if not _source_matches_stage(source, outfit=outfit, identity_hash=identity_hash):
        return False
    if not _fits_target_area(
        row.width, row.height, asset_kind=row.asset_kind, target_width=target_width, target_height=target_height
    ):
        return False
    return row.asset_kind != "actor" or actor_body_ready(row.body_json, row.clip_kind)


async def creation_request(user_id: int, request_id: str) -> tuple[VisualJobView, VisualSnapshot] | None:
    async with SESSION_LOCAL() as db:
        job_id = await requested_job_id(db, user_id, request_id)
        job = (
            await db.scalar(select(VisualJob).where(VisualJob.id == job_id, VisualJob.user_id == user_id))
            if job_id is not None
            else None
        )
        return (job_view(job), visual_snapshot(job)) if job is not None else None


async def job_review_reason(user_id: int, job_id: int) -> str:
    """已结束制作的视频检查结论，用于记录成品不可自动使用的原因。"""

    async with SESSION_LOCAL() as db:
        reason = await db.scalar(
            select(VisualVersion.snapshot_json["review_reason"].astext)
            .join(VisualJob, VisualJob.result_version_id == VisualVersion.id)
            .where(VisualJob.id == job_id, VisualJob.user_id == user_id)
        )
    return reason or ""


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
    """基础准备只复用身份、造型、动作版本相符且已确认可自动使用的透明成品。

    准备判断与目录使用相同资格，检查所有已发布版本。
    """

    async with SESSION_LOCAL() as db:
        rows = await db.execute(
            select(VisualVersion.media_path, VisualVersion.snapshot_json, VisualVersion.body_json)
            .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
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
            if (
                not isinstance(frozen, (ActorClipSource, AnalyzingVisualSource))
                or not body
                or frozen.outfit is None
                or frozen.action is None
            ):
                continue
            if frozen.kind == "analysis" and (frozen.asset_kind != "actor" or not frozen.presentation_approved):
                continue
            if (
                frozen.identity_hash == source.identity_hash
                and frozen.portrait_hash == source.portrait_hash
                and frozen.identity_reference_role == source.identity_reference_role
                and frozen.outfit.content_hash == source.outfit.content_hash
                and frozen.action.media_basis() == source.action.media_basis()
                and automatic_media_ready(
                    media_kind="video",
                    asset_kind="actor",
                    media_path=path,
                    auto_selectable=_frozen_auto_selectable(frozen),
                    clip_kind=frozen.action.clip_kind,
                    body=body,
                    presentation_approved=frozen.presentation_approved,
                )
            ):
                return True
    return False


def version_body(item: VisualItem | PresentedVisual) -> ActorClipBody | None:
    """actor 版本的主体元数据；路径转为客户端地址，与 media_url 同口径。"""

    body_json = item.body_json
    if not body_json:
        return None
    body = ActorClipBody.model_validate(body_json)
    return client_body(body)


def item_view(item: VisualItem | PresentedVisual, snapshot: VisualSnapshot | None = None) -> VisualItemView:
    snapshot = snapshot or visual_snapshot(item)
    source = snapshot.source
    outfit = source.outfit if source.kind in ("actor", "cinematic", "analysis") else None
    scene = source.scene if source.kind in ("scene", "cinematic", "analysis") else None
    action = source.action if source.kind in ("actor", "cinematic", "analysis") else None
    return VisualItemView(
        id=item.id,
        media_kind=item.media_kind,
        asset_kind=item.asset_kind,
        clip_kind=visual_clip_kind(item, snapshot),
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
        body=version_body(item),
        original_url=client_asset_url(source.original_path) if source.original_path else None,
        auto_playable=automatic_media_ready(
            media_kind=item.media_kind,
            asset_kind=item.asset_kind,
            media_path=item.media_path,
            auto_selectable=_frozen_auto_selectable(source),
            clip_kind=visual_clip_kind(item, snapshot),
            body=item.body_json,
            presentation_approved=source.presentation_approved,
        ),
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
        target_kind=job.target_kind,
        asset_kind=job.asset_kind,
        action_name=_job_action_name(job.snapshot_json),
        preparation=progress is not None and progress.source == "base_preparation",
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
            filters.append(
                or_(
                    VisualItem.title.ilike(pattern),
                    VisualItem.description.ilike(pattern),
                    VisualVersion.snapshot_json["source"]["original_filename"].astext.ilike(pattern),
                )
            )
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
            func.coalesce(VisualVersion.snapshot_json["source"]["outfit"]["name"].astext, "").label("outfit_name"),
            func.coalesce(VisualVersion.snapshot_json["source"]["scene"]["title"].astext, "").label("scene_title"),
            VisualVersion.snapshot_json["source"]["action"]["name"].astext.label("action_name"),
            VisualVersion.snapshot_json["source"]["media_semantics"]["auto_selectable"].astext.label("auto_selectable"),
            VisualVersion.body_json,
            VisualVersion.snapshot_json["source"]["presentation_approved"].as_boolean().label("presentation_approved"),
            VisualVersion.snapshot_json["source"]["original_path"].astext.label("original_path"),
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
        analysis_states = dict(
            (
                await db.execute(
                    select(VisualImportJob.visual_id, VisualImportJob.state).where(
                        VisualImportJob.user_id == user_id,
                        VisualImportJob.visual_id.in_([row["id"] for row in rows[:limit]]),
                    )
                )
            ).all()
        )
        views = []
        for row in rows[:limit]:
            values = dict(row)
            values["analysis_state"] = analysis_states.get(values["id"])
            values["media_url"] = client_asset_url(values.pop("media_path"))
            poster = values.pop("poster_path")
            values["poster_url"] = client_asset_url(poster) if poster else None
            original_path = values.pop("original_path")
            values["original_url"] = client_asset_url(original_path) if original_path else None
            values["auto_playable"] = automatic_media_ready(
                media_kind=values["media_kind"],
                asset_kind=values["asset_kind"],
                media_path=row["media_path"],
                auto_selectable=values.pop("auto_selectable") == "true",
                clip_kind=values["clip_kind"],
                body=values.pop("body_json"),
                presentation_approved=bool(values.pop("presentation_approved")),
            )
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
        analysis_job = await db.scalar(
            select(VisualImportJob).where(VisualImportJob.user_id == user_id, VisualImportJob.visual_id == visual_id)
        )
        analysis = import_job_view(analysis_job) if analysis_job else None
        if analysis and analysis.result:
            analysis = analysis.model_copy(
                update={
                    "result": analysis.result.model_copy(
                        update={
                            "components": [
                                entry.model_copy(
                                    update={
                                        "preview_path": client_asset_url(entry.preview_path)
                                        if entry.preview_path
                                        else None
                                    }
                                )
                                for entry in analysis.result.components
                            ]
                        }
                    )
                }
            )
        return VisualDetailView(
            **item_view(item, snapshot).model_dump(),
            snapshot=snapshot,
            replaced_at_version=item.version - 1 if item.version > 1 else None,
            jobs=[job_view(job) for job in jobs],
            analysis=analysis,
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
            func.coalesce(VisualJob.checkpoint["source"].as_string() == "base_preparation", False).label("preparation"),
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


async def presentation_state(user_id: int, *, with_catalog: bool = True) -> VisualPresentationView:
    """呈现状态装配；内部决策路径不需要目录时跳过（目录含逐行文件核对）。"""

    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if row is None:
            return VisualPresentationView()
        current = presented_item(row)
        loop = presented_item(row, loop=True)
        pending = _pending_command(row)
        composition = stage_composition(row)
        static_raw = row.pending_static_json or row.current_static_json
        static = StagePresentation.model_validate(static_raw) if static_raw else None
        scene = static.scene if static else composition.scene_material if composition else None
        return VisualPresentationView(
            composition=await composition_with_media(db, user_id, composition),
            ambient_actor=await _ambient_actor(db, row),
            ambient_catalog=await ambient_catalog(db, row) if with_catalog else [],
            ambient_scene=await ambient_scene_catalog(db, user_id, scene) if with_catalog else [],
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


async def _layer_media(
    db: AsyncSession, user_id: int, visual_id: int | None, version: int | None
) -> StageLayerMedia | None:
    """按组合引用的版本装配该层可播媒体；引用缺失或文件不完整时返回 None。"""

    if visual_id is None or version is None:
        return None
    row = (
        await db.execute(
            select(VisualItem, VisualVersion)
            .join(VisualVersion, VisualVersion.visual_id == VisualItem.id)
            .where(
                VisualItem.id == visual_id,
                VisualItem.user_id == user_id,
                VisualVersion.number == version,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    item, version_row = row
    # 存在性按引用版本自身的文件核对；组合可能引用非当前版本。
    if existing_asset(version_row.media_path) is None:
        return None
    source = version_row.snapshot_json.get("source") or {}
    scene = source.get("scene") or {}
    return StageLayerMedia(
        visual_id=item.id,
        version=version_row.number,
        media_kind=item.media_kind,
        asset_kind=item.asset_kind,
        clip_kind=visual_clip_kind(version_view(item, version_row)),
        media_url=client_asset_url(version_row.media_path),
        poster_url=client_asset_url(version_row.poster_path) if version_row.poster_path else None,
        width=version_row.width,
        height=version_row.height,
        duration_ms=version_row.duration_ms,
        description=version_row.description,
        body=ActorClipBody.model_validate(version_row.body_json) if version_row.body_json else None,
        day_phase=source.get("day_phase") if source.get("kind") == "scene" else None,
        scene_content_hash=scene.get("content_hash"),
    )


def frozen_stage_reference(state: VisualPresentation) -> tuple[ComboMaterialRef | None, str | None]:
    """当前有效舞台冻结的造型与身份参照；静态目标与组合取其一。"""

    static_raw = state.pending_static_json or state.current_static_json
    static = StagePresentation.model_validate(static_raw) if static_raw else None
    if static is not None and static.outfit is not None and static.identity_hash is not None:
        return static.outfit, static.identity_hash
    outfit = presentation_outfit(state)
    current = presented_item(state)
    source = visual_snapshot(current).source if current else None
    composition = stage_composition(state)
    if current is not None and visual_is_short_action(current) and composition is not None:
        return outfit, composition.identity_hash
    if source is not None and (
        source.kind in ("actor", "cinematic") or (source.kind == "analysis" and source.asset_kind != "scene")
    ):
        return (source.outfit if outfit is None else outfit), source.identity_hash
    return outfit, composition.identity_hash if composition else None


async def ambient_catalog(db: AsyncSession, state: VisualPresentation, *, limit: int = 24) -> list[AmbientCatalogEntry]:
    """常驻、互动、生活影像分别限额；内置动作取最新可自动使用的版本。

    不合格的新版本仍保留在影像库；同一动作缺少合格版本时不出现在目录里。
    """

    outfit, identity_hash = frozen_stage_reference(state)
    if outfit is None or identity_hash is None:
        return []
    preset_keys = await action_preset_keys(db, state.user_id)
    preset_order = {
        action_id: index
        for index, action_id in enumerate(
            sorted(preset_keys, key=lambda action_id: (preset_keys[action_id] != "idle", action_id))
        )
    }
    query = (
        select(
            VisualItem.id,
            VisualItem.asset_kind,
            VisualItem.clip_kind,
            VisualItem.action_id,
            VisualItem.media_kind,
            VisualVersion.number,
            VisualVersion.media_path,
            VisualVersion.poster_path,
            VisualVersion.width,
            VisualVersion.height,
            VisualVersion.duration_ms,
            VisualVersion.description,
            VisualVersion.body_json,
            VisualVersion.snapshot_json["source"].label("source"),
        )
        .select_from(VisualItem)
        .join(VisualVersion, VisualVersion.visual_id == VisualItem.id)
        .where(
            VisualItem.user_id == state.user_id,
            VisualItem.status == "ready",
            or_(VisualItem.media_kind == "video", VisualVersion.snapshot_json["source"]["kind"].astext == "analysis"),
            VisualVersion.snapshot_json["source"]["identity_hash"].astext == identity_hash,
            VisualVersion.snapshot_json["source"]["outfit"]["content_hash"].astext == outfit.content_hash,
        )
    )
    entries: list[AmbientCatalogEntry] = []
    for asset_kind, clip_kind in (("actor", "loop"), ("actor", "once"), ("cinematic", None)):
        filters = [VisualItem.asset_kind == asset_kind]
        if clip_kind is not None:
            filters.append(VisualItem.clip_kind == clip_kind)
        order = [VisualVersion.id.desc()]
        if asset_kind == "actor" and preset_order:
            order.insert(0, case(preset_order, value=VisualItem.action_id, else_=len(preset_order)))
        rows = await db.stream(query.where(*filters).order_by(*order), execution_options={"yield_per": 32})
        selected_presets: set[str] = set()
        selected_items: set[int] = set()
        count = 0
        try:
            async for row in rows:
                preset = preset_keys.get(row.action_id)
                if row.id in selected_items or (preset and asset_kind == "actor" and preset in selected_presets):
                    continue
                source = row.source or {}
                if not automatic_media_ready(
                    media_kind=row.media_kind,
                    asset_kind=row.asset_kind,
                    media_path=row.media_path,
                    auto_selectable=(source.get("media_semantics") or {}).get("auto_selectable"),
                    clip_kind=row.clip_kind,
                    body=row.body_json,
                    presentation_approved=bool(source.get("presentation_approved")),
                ):
                    continue
                if not _stage_ready(
                    row,
                    outfit=outfit,
                    identity_hash=identity_hash,
                    target_width=state.target_width,
                    target_height=state.target_height,
                ):
                    continue
                action = ActionMaterialRef.model_validate(source["action"]) if source.get("action") else None
                semantics = source.get("media_semantics") or {}
                if action is None and (row.media_kind != "image" or not semantics):
                    continue
                entries.append(
                    AmbientCatalogEntry(
                        visual_id=row.id,
                        version=row.number,
                        media_kind=row.media_kind,
                        asset_kind=row.asset_kind,
                        clip_kind=(row.clip_kind or "loop") if row.media_kind == "video" else None,
                        media_url=client_asset_url(row.media_path),
                        poster_url=client_asset_url(row.poster_path) if row.poster_path else None,
                        width=row.width,
                        height=row.height,
                        duration_ms=row.duration_ms,
                        description=row.description,
                        body=ActorClipBody.model_validate(row.body_json) if row.body_json else None,
                        action_id=row.action_id,
                        action_name=action.name if action else "",
                        preset_key=preset,
                        semantic_tags=action.semantic_tags if action else semantics.get("semantic_tags", []),
                        performance_role=action.performance_role
                        if action and row.media_kind == "video"
                        else "sustained",
                        motion_intensity=action.motion_intensity
                        if action
                        else semantics.get("motion_intensity", "subtle"),
                        interruptible=action.interruptible if action else True,
                        media_semantics=source.get("media_semantics"),
                        allowed_triggers=action.allowed_triggers
                        if action and row.media_kind == "video"
                        else ["manual", "periodic"],
                        use_when=action.use_when if action else [],
                        avoid_when=action.avoid_when if action else [],
                    )
                )
                selected_items.add(row.id)
                if preset:
                    selected_presets.add(preset)
                count += 1
                if count >= limit:
                    break
        finally:
            await rows.close()
    return entries


async def _ambient_actor(db: AsyncSession, state: VisualPresentation) -> StageLayerMedia | None:
    """只为冻结的当前造型投影已有且可自动使用的待机成品；不制作、不消费演出指令。"""

    outfit, identity_hash = frozen_stage_reference(state)
    if outfit is None or identity_hash is None:
        return None
    idle = await action_by_preset_key(db, state.user_id, "idle")
    if idle is None:
        return None
    versions = await db.scalars(
        select(VisualVersion)
        .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
        .where(
            VisualItem.user_id == state.user_id,
            VisualItem.asset_kind == "actor",
            VisualItem.status == "ready",
            VisualItem.action_id == idle.id,
            VisualItem.clip_kind == "loop",
            VisualVersion.snapshot_json["source"]["identity_hash"].astext == identity_hash,
            VisualVersion.snapshot_json["source"]["outfit"]["content_hash"].astext == outfit.content_hash,
        )
        .order_by(VisualVersion.id.desc())
    )
    for version in versions:
        frozen = VisualSnapshot.model_validate(version.snapshot_json).source
        if (
            isinstance(frozen, (ActorClipSource, AnalyzingVisualSource))
            and (frozen.kind != "analysis" or (frozen.asset_kind == "actor" and frozen.presentation_approved))
            and frozen.outfit is not None
            and frozen.action is not None
            and frozen.identity_hash == identity_hash
            and frozen.outfit.content_hash == outfit.content_hash
            and frozen.action.clip_kind == "loop"
            and automatic_media_ready(
                media_kind="video",
                asset_kind="actor",
                media_path=version.media_path,
                auto_selectable=_frozen_auto_selectable(frozen),
                clip_kind=frozen.action.clip_kind,
                body=version.body_json,
                presentation_approved=frozen.presentation_approved,
            )
        ):
            return await _layer_media(db, state.user_id, version.visual_id, version.number)
    return None


async def ambient_scene_catalog(db: AsyncSession, user_id: int, scene: SceneMediaRef | None) -> list[StageLayerMedia]:
    """按当前环境的冻结内容取各时段最新可播视频，其他环境和失效文件不占名额。"""

    if scene is None:
        return []
    rows = await db.stream(
        select(
            VisualItem.id,
            VisualVersion.number,
            VisualVersion.media_path,
            VisualVersion.poster_path,
            VisualVersion.width,
            VisualVersion.height,
            VisualVersion.duration_ms,
            VisualVersion.description,
            VisualVersion.snapshot_json["source"]["day_phase"].astext.label("day_phase"),
        )
        .select_from(VisualItem)
        .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
        .where(
            VisualItem.user_id == user_id,
            VisualItem.status == "ready",
            VisualItem.media_kind == "video",
            VisualItem.asset_kind == "scene",
            VisualItem.clip_kind == "loop",
            VisualVersion.snapshot_json["source"]["scene"]["content_hash"].astext == scene.content_hash,
            or_(
                VisualVersion.snapshot_json["source"]["kind"].astext != "analysis",
                VisualVersion.snapshot_json["source"]["presentation_approved"].as_boolean().is_(True),
            ),
        )
        .order_by(VisualVersion.id.desc()),
        execution_options={"yield_per": 32},
    )
    entries: list[StageLayerMedia] = []
    phases: set[str | None] = set()
    try:
        async for row in rows:
            if row.day_phase in phases or existing_asset(row.media_path) is None:
                continue
            entries.append(
                StageLayerMedia(
                    visual_id=row.id,
                    version=row.number,
                    media_kind="video",
                    asset_kind="scene",
                    clip_kind="loop",
                    media_url=client_asset_url(row.media_path),
                    poster_url=client_asset_url(row.poster_path) if row.poster_path else None,
                    width=row.width,
                    height=row.height,
                    duration_ms=row.duration_ms,
                    description=row.description,
                    day_phase=row.day_phase,
                    scene_content_hash=scene.content_hash,
                )
            )
            phases.add(row.day_phase)
            if len(phases) == 5:
                break
    finally:
        await rows.close()
    return entries


async def composition_with_media(
    db: AsyncSession, user_id: int, composition: StageComposition | None
) -> StageComposition | None:
    """组合引用保持纯 ID；媒体投影放在 media 字段供客户端直接消费。"""

    if composition is None:
        return None
    media = None
    if composition.mode == "layered":
        media = StageCompositionMedia(
            mode="layered",
            scene=await _layer_media(db, user_id, composition.scene_visual_id, composition.scene_version),
            actor=await _layer_media(db, user_id, composition.actor.visual_id, composition.actor.version)
            if composition.actor
            else None,
        )
    else:
        media = StageCompositionMedia(
            mode="cinematic",
            cinematic=await _layer_media(db, user_id, composition.cinematic.visual_id, composition.cinematic.version)
            if composition.cinematic
            else None,
        )
    return composition.model_copy(update={"media": media})


def _pending_command(row: VisualPresentation) -> VisualPlayCommand | None:
    """待播指令过期后不再提供；换号与模式切换在认领时重新校验。"""

    if not row.pending_command_json:
        return None
    command = VisualPlayCommand.model_validate(row.pending_command_json)
    return None if ensure_utc(command.expires_at) <= utc_now() else command


async def _presentation_snapshot(user_id: int, *, for_prompt: bool) -> ActualPresentationSnapshot:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        item = presented_item(row) if row else None
        if row and row.current_static_json and item is None:
            static = StagePresentation.model_validate(row.current_static_json)
            if static.outfit is None or static.scene is None:
                return ActualPresentationSnapshot()
            presence = get_presentation_snapshot(user_id)
            outfit_path = existing_asset(static.outfit.path)
            scene_path = existing_asset(static.scene.path)
            return ActualPresentationSnapshot(
                outfit_path=outfit_path,
                outfit_hash=static.outfit.content_hash if outfit_path else None,
                outfit_description=static.outfit.description,
                scene_path=scene_path,
                scene_hash=static.scene.content_hash if scene_path else None,
                scene_description=static.scene.description,
                identity_path=existing_asset(static.identity_path),
                identity_hash=static.identity_hash,
                ready=static.receipt_kind is not None
                and presence is not None
                and static.presentation_epoch == presence.revision
                and static.receipt_kind == ("window_static" if presence.mode == "window" else "desktop_static")
                and outfit_path is not None
                and scene_path is not None
                and get_user_proactive_record(user_id).available,
            )
        if item is None or item.user_id != user_id:
            return ActualPresentationSnapshot()
        snapshot = visual_snapshot(item)
        presence = get_presentation_snapshot(user_id)
        show_full_visual = (
            for_prompt
            or (presence is not None and presence.mode == "desktop")
            or (item.asset_kind == "actor" and item.receipt_kind == "video")
        )
        visual_path = existing_asset(
            item.media_path if item.media_kind == "image" else item.input_image_path or item.poster_path,
        )
        source = snapshot.source
        composition = stage_composition(row) if row else None
        character_source = source.kind in ("actor", "cinematic") or (
            source.kind == "analysis" and source.asset_kind in ("actor", "cinematic")
        )
        environment_source = source.kind in ("scene", "cinematic") or (
            source.kind == "analysis" and source.asset_kind in ("scene", "cinematic")
        )
        outfit = source.outfit if character_source else composition.outfit_material if composition else None
        scene = source.scene if environment_source else composition.scene_material if composition else None
        outfit_path = existing_asset(outfit.path) if outfit else None
        scene_path = existing_asset(scene.path) if scene else None
        identity_path = existing_asset(
            source.identity_path if character_source else composition.identity_path if composition else None
        )
        outfit_description = (item.outfit_description if show_full_visual else "") or (
            outfit.description if outfit else ""
        )
        expected_receipt = (
            "window_static"
            if presence is not None and presence.mode == "window" and item.asset_kind != "actor"
            else item.media_kind
        )
        valid_receipt = item.receipt_kind == expected_receipt or (
            presence is not None and presence.mode == "window" and item.receipt_kind == "window_static"
        )
        return ActualPresentationSnapshot(
            visual_id=item.id,
            media_kind=item.media_kind,
            asset_kind=item.asset_kind,
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
            identity_hash=(
                source.identity_hash if character_source else composition.identity_hash if composition else None
            )
            if identity_path
            else None,
            identity_reference_role=source.identity_reference_role if character_source else "portrait",
            ready=presence is not None
            and row is not None
            and row.presentation_epoch == presence.revision
            and item.presentation_epoch == presence.revision
            and valid_receipt
            and get_user_proactive_record(user_id).available
            and visual_playable(item),
        )


async def actual_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """实际呈现快照：冻结造型与环境资料加成品描述；素材缺失的字段为空。"""

    return await _presentation_snapshot(user_id, for_prompt=False)


async def prompt_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """供伙伴判断和媒体策划使用的模式无关快照。"""

    return await _presentation_snapshot(user_id, for_prompt=True)


async def visual_play_catalog(user_id: int, trigger: str) -> list[VisualPlayOption]:
    """按动作触发条件过滤可播影像；播放方式与条件来自冻结输入的动作引用。"""

    async with SESSION_LOCAL() as db:
        state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        outfit, identity_hash = frozen_stage_reference(state) if state is not None else (None, None)
        rows = await db.execute(
            select(
                VisualItem.id,
                VisualItem.media_kind,
                VisualItem.asset_kind,
                VisualItem.clip_kind,
                VisualVersion.media_path,
                VisualVersion.width,
                VisualVersion.height,
                VisualVersion.body_json,
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
            if not _stage_ready(
                row,
                outfit=outfit,
                identity_hash=identity_hash,
                target_width=state.target_width if state is not None else None,
                target_height=state.target_height if state is not None else None,
            ):
                continue
            source = row.source
            action = (
                ActionMaterialRef.model_validate(source["action"])
                if source["kind"] in ("actor", "cinematic", "analysis") and source.get("action")
                else None
            )
            options.append(
                VisualPlayOption(
                    visual_id=row.id,
                    name=action.name if action is not None else "",
                    media_kind=row.media_kind,
                    asset_kind=row.asset_kind,
                    clip_kind=row.clip_kind,
                    duration_seconds=action.duration_seconds if action is not None else None,
                    description=row.description,
                    outfit_description=row.outfit_description,
                    scene_description=row.scene_description or "",
                    semantic_tags=action.semantic_tags if action is not None else [],
                    performance_role=action.performance_role if action is not None else "idle",
                    motion_intensity=action.motion_intensity if action is not None else "normal",
                    media_semantics=source.get("media_semantics"),
                    use_when=action.use_when if action is not None else [],
                    avoid_when=action.avoid_when if action is not None else [],
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
    """动作详情的媒体实现清单：该动作已制作的可播成品投影。

    `stage_ready` 标记该实现与当前舞台冻结身份、造型一致且文件完整——用户据此
    区分“这个动作现在能直接播”与“属于其他造型的历史版本”。
    """

    async with SESSION_LOCAL() as db:
        state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        outfit, identity_hash = frozen_stage_reference(state) if state is not None else (None, None)
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
            VisualVersion.media_path,
            VisualVersion.body_json,
            VisualVersion.width,
            VisualVersion.height,
            VisualVersion.snapshot_json["source"].label("source"),
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
            ).all()
        )
        media = [
            ActionMediaEntry(
                visual_id=row.id,
                media_kind=row.media_kind,
                asset_kind=row.asset_kind,
                clip_kind=row.clip_kind,
                title=row.title,
                outfit_id=row.outfit_id,
                outfit_name=row.outfit_name or "",
                version=row.version,
                stage_ready=_stage_ready(
                    row,
                    outfit=outfit,
                    identity_hash=identity_hash,
                    target_width=state.target_width if state else None,
                    target_height=state.target_height if state else None,
                )
                and existing_asset(row.media_path) is not None,
                updated_at=row.updated_at,
            )
            for row in rows[:limit]
        ]
        return ActionMediaListResponse(
            media=media,
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None,
            limit=limit,
        )
