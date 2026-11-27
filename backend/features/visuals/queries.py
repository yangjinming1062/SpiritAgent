"""影像库的只读查询与对外视图装配；签名 URL 只在响应出口生成。"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from infrastructure.ai.decision import decision_enabled
from infrastructure.assets.asset_store import client_asset_url
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import utc_now
from runtime.presentation import visual_surface_accepts
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from features.action_library.queries import action_by_preset_key, action_preset_keys
from features.companion.planning_queries import planning_persona
from features.companion.proactive_runtime import get_presentation_snapshot
from features.companion.rhythm import RhythmView, read_rhythm
from features.jobs.service import requested_job_id
from features.life.queries import active_pursuit_title, get_life_state_view
from features.media.chain import MediaChainState
from features.media.review_contracts import VisualReview
from features.media.video_queue.contracts import VideoQueueInfo
from features.media.video_queue.service import video_attempt_queue_info
from features.media.visual_semantics import DayPhase

from .client_assets import client_body
from .eligibility import actor_body_ready, automatic_media_blocks, automatic_media_ready, existing_asset
from .geometry import visual_aspect_matches
from .import_jobs import import_job_view
from .import_operations import import_analysis_state
from .models import VisualImportJob, VisualItem, VisualJob, VisualPresentation, VisualVersion
from .policy import full_stage_rest_in_progress
from .ports import read_stage_scene, request_stage_recovery, scene_reference_exists
from .quality import quality_failure_message
from .repository import (
    VisualNotFoundError,
    active_pending_command,
    displayed_rest_transition,
    presentation_outfit,
    presented_frame,
    presented_item,
    stage_composition,
    version_view,
    visual_clip_kind,
    visual_is_short_action,
    visual_job_row,
    visual_playable,
    visual_plays_as_loop,
    visual_snapshot,
)
from .schemas import (
    ActionMaterialRef,
    ActionMediaEntry,
    ActionMediaListResponse,
    ActorClipBody,
    ActorClipRef,
    ActorClipSource,
    ActualPresentationSnapshot,
    AmbientCatalogEntry,
    AnalyzingVisualSource,
    CinematicSource,
    ComboMaterialRef,
    DefaultWallpaperView,
    ImportAnalysisView,
    PresentedScene,
    PresentedVisual,
    SceneMediaRef,
    StageComposition,
    StageCompositionLayout,
    StageCompositionMedia,
    StageIntent,
    StageLayerMedia,
    StagePlaybackPlan,
    StagePresentation,
    VisualAutoPlayBlock,
    VisualDetailView,
    VisualItemView,
    VisualJobListResponse,
    VisualJobState,
    VisualJobView,
    VisualListResponse,
    VisualMediaKind,
    VisualPlayOption,
    VisualPresentationView,
    VisualSnapshot,
    VisualSource,
    carries_character,
    carries_scene,
    parse_job_progress,
)
from .stage_selection import (
    StageMediaCandidate,
    actor_options,
    cinematic_options,
    compatible_activity,
    paired_cinematic_wake,
    paired_wake,
    phase_compatible,
    project_stage_intent,
    rank_stage_variants,
    scene_matches,
    stage_activity,
    stage_day_phase,
    stage_scene_alias,
)

_STAGE_PREFERENCE_WINDOW_SECONDS = 4.0
_STAGE_CINEMATIC_LIMIT = 6
_STAGE_REACTION_LIMIT = 24
_STAGE_REACTION_TRIGGERS = ("chat", "user_return")
_LIBRARY_SECTIONS = {"complete": ("scene", "cinematic"), "sprite": ("actor",)}


def _frozen_asset_kind() -> ColumnElement[str]:
    source = VisualVersion.snapshot_json["source"]
    return case(
        (source["kind"].astext == "analysis", func.coalesce(source["asset_kind"].astext, "cinematic")),
        else_=source["kind"].astext,
    )


def _frozen_clip_kind() -> ColumnElement[str | None]:
    source = VisualVersion.snapshot_json["source"]
    return case(
        (VisualItem.media_kind == "image", None),
        (source["kind"].astext == "analysis", source["clip_kind"].astext),
        (source["kind"].astext == "scene", "loop"),
        else_=source["action"]["clip_kind"].astext,
    )


def _source_matches_stage(source: dict, *, outfit: ComboMaterialRef | None, identity_hash: str | None) -> bool:
    """只让当前身份/造型的伙伴成品进入舞台清单；环境成品不绑定伙伴。"""

    if not source.get("presentation_approved"):
        return False
    if source.get("kind") == "scene":
        return bool(source.get("scene"))
    if (
        source.get("kind") == "analysis"
        and source.get("asset_kind") in {"scene", "cinematic"}
        and not source.get("scene")
    ):
        return False
    return bool(
        outfit is not None
        and identity_hash
        and source.get("identity_hash") == identity_hash
        and (source.get("outfit") or {}).get("content_hash") == outfit.content_hash
    )


def _frozen_auto_selectable(source: VisualSource) -> bool | None:
    """冻结输入里的自动可播结论；环境媒体与未知语义为 None。"""

    semantics = getattr(source, "media_semantics", None)
    return semantics.auto_selectable if semantics is not None else None


def _fits_target_area(
    width: int, height: int, *, asset_kind: str, target_width: int | None, target_height: int | None
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
        return (await job_view(db, job), visual_snapshot(job)) if job is not None else None


async def pending_base_preparation_actions(
    db: AsyncSession, user_id: int, *, identity_hash: str, outfit_hash: str, portrait_hash: str, reference_role: str
) -> list[int]:
    """优先回到当前造型上此前已受理、仍未成功的基础动作。"""
    source = VisualJob.snapshot_json["source"]
    return list(
        await db.scalars(
            select(VisualJob.action_id)
            .where(
                VisualJob.user_id == user_id,
                VisualJob.action_id.is_not(None),
                VisualJob.state != "succeeded",
                VisualJob.checkpoint["source"].astext == "base_preparation",
                source["identity_hash"].astext == identity_hash,
                source["outfit"]["content_hash"].astext == outfit_hash,
                source["portrait_hash"].astext == portrait_hash,
                source["identity_reference_role"].astext == reference_role,
            )
            .order_by(VisualJob.id)
        )
    )


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
        return await read_current_outfit_reference(db, user_id)


async def read_current_outfit_reference(db: AsyncSession, user_id: int) -> ComboMaterialRef | None:
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


async def reusable_actor_clip(
    user_id: int, source: ActorClipSource, *, opening_aspect: tuple[int, int] | None = None
) -> tuple[int, int, ActorClipBody] | None:
    """基础准备只复用身份、造型、动作版本相符且已确认可自动使用的透明成品。

    准备判断与目录使用相同资格，检查所有已发布版本。
    """

    rhythm_tags = {"sleeping", "waking"}.intersection(source.action.semantic_tags)
    opening_hash = source.opening_frame.content_hash if source.opening_frame is not None else None
    async with SESSION_LOCAL() as db:
        rows = await db.execute(
            select(
                VisualVersion.visual_id,
                VisualVersion.number,
                VisualVersion.width,
                VisualVersion.height,
                VisualVersion.media_path,
                VisualVersion.snapshot_json,
                VisualVersion.body_json,
                VisualVersion.content_hash,
            )
            .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
            .where(
                VisualItem.user_id == user_id,
                VisualItem.asset_kind == "actor",
                VisualItem.media_kind == "video",
                VisualItem.status == "ready",
                VisualItem.outfit_id == source.outfit.material_id,
                VisualItem.action_id == source.action.action_id,
            )
            .order_by(VisualVersion.id.desc())
        )
        for visual_id, number, width, height, path, snapshot_json, body, content_hash in rows:
            if opening_aspect is not None and not visual_aspect_matches(width, height, *opening_aspect):
                continue
            snapshot = VisualSnapshot.model_validate(snapshot_json)
            frozen = snapshot.source
            if (
                not isinstance(frozen, (ActorClipSource, AnalyzingVisualSource))
                or not body
                or frozen.outfit is None
                or frozen.action is None
            ):
                continue
            if frozen.kind == "analysis" and (frozen.asset_kind != "actor" or not frozen.presentation_approved):
                continue
            semantics = frozen.media_semantics
            if rhythm_tags and (
                semantics is None
                or semantics.scene_tags
                or semantics.motion_intensity != "subtle"
                or not rhythm_tags.issubset(semantics.semantic_tags)
            ):
                continue
            if opening_hash is not None and (
                frozen.kind != "actor"
                or frozen.opening_frame is None
                or frozen.opening_frame.content_hash != opening_hash
            ):
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
                    clip_kind=frozen.clip_kind if frozen.kind == "analysis" else frozen.action.clip_kind,
                    body=body,
                    presentation_approved=frozen.presentation_approved,
                    review_result=snapshot.review_result,
                    reference_source=frozen.model_dump(mode="json"),
                    media_content_hash=content_hash,
                    action_clip_kind=frozen.action.clip_kind,
                )
            ):
                return visual_id, number, ActorClipBody.model_validate(body)
    return None


def version_body(item: VisualItem | PresentedVisual) -> ActorClipBody | None:
    """actor 版本的主体元数据；路径转为客户端地址，与 media_url 同口径。"""

    body_json = item.body_json
    if not body_json:
        return None
    body = ActorClipBody.model_validate(body_json)
    return client_body(body)


def _item_auto_play_blocks(item: VisualItem | PresentedVisual, snapshot: VisualSnapshot) -> list[VisualAutoPlayBlock]:
    source = snapshot.source
    action = source.action if source.kind in ("actor", "cinematic", "analysis") else None
    return automatic_media_blocks(
        media_kind=item.media_kind,
        asset_kind=item.asset_kind,
        media_path=item.media_path,
        auto_selectable=_frozen_auto_selectable(source),
        clip_kind=visual_clip_kind(item, snapshot),
        body=item.body_json,
        presentation_approved=source.presentation_approved,
        review_result=snapshot.review_result,
        reference_source=source.model_dump(mode="json"),
        media_content_hash=item.content_hash,
        scene_reference=getattr(source, "scene", None) is not None,
        action_clip_kind=action.clip_kind if action else None,
    )


def _import_block_next_step(analysis: ImportAnalysisView, code: str) -> str:
    # 重试或恢复时，结果里可能还保留上一轮的阻塞原因；当前任务状态优先。
    if analysis.state in {"queued", "running"}:
        return "continue_analysis"
    if analysis.stage == "review_pending":
        return "review_candidate" if analysis.manual_review_available else "continue_analysis"
    if analysis.state in {"paused", "cancelled", "failed"} and analysis.stage not in {"confirmation"}:
        return "continue_analysis"
    if code == "animated":
        return "replace_media"
    if code == "identity":
        return "identity_setup"
    if code in {"semantics", "timing", "review"}:
        return "reanalyze"
    if code in {"materials_incomplete", "outfit", "scene", "action"} and (
        analysis.state == "awaiting_input" or analysis.stage == "confirmation"
    ):
        return "confirm_materials"
    if analysis.state in {"queued", "running", "paused", "cancelled", "failed"}:
        return "continue_analysis"
    return "none"


def _merge_import_auto_play_blocks(
    eligibility_blocks: list[VisualAutoPlayBlock], import_blocks: list[VisualAutoPlayBlock]
) -> list[VisualAutoPlayBlock]:
    """用导入任务的细节替换同类通用提示，同时保留文件和主体等独立阻塞。"""

    import_codes = {block.code for block in import_blocks}
    return [block for block in eligibility_blocks if block.code not in import_codes] + import_blocks


def _import_auto_play_blocks(analysis: ImportAnalysisView) -> list[VisualAutoPlayBlock]:
    if analysis.result and analysis.result.blocking_reasons:
        code_map = {
            "materials_incomplete": "materials_incomplete",
            "identity": "identity",
            "outfit": "materials_incomplete",
            "scene": "materials_incomplete",
            "action": "materials_incomplete",
            "semantics": "semantics_unclear",
            "timing": "timing_mismatch",
            "review": "presentation_review",
            "animated": "unsupported_media",
        }
        blocks = [
            VisualAutoPlayBlock(
                code=code_map[block.code],
                reason=block.reason,
                next_step=_import_block_next_step(analysis, block.code),
            )
            for block in analysis.result.blocking_reasons
            if block.code in code_map
        ]
        if blocks:
            return blocks
    if analysis.state == "awaiting_input":
        return [
            VisualAutoPlayBlock(
                code="analysis_pending",
                reason="请先确认下方识别到的衣着、环境或动作，确认后会继续检查自动演出条件",
                next_step="confirm_materials",
            )
        ]
    if analysis.state in {"queued", "running"}:
        return [
            VisualAutoPlayBlock(
                code="analysis_pending",
                reason="影像仍在整理和检查中，完成后会重新判断自动演出资格",
                next_step="continue_analysis",
            )
        ]
    if analysis.state in {"paused", "cancelled", "failed"}:
        return [
            VisualAutoPlayBlock(
                code="analysis_pending",
                reason="影像整理尚未完成，原件和已取得的结果仍已保留",
                next_step="continue_analysis",
            )
        ]
    return []


def item_view(
    item: VisualItem | PresentedVisual,
    snapshot: VisualSnapshot | None = None,
    *,
    auto_play_blocks: list[VisualAutoPlayBlock] | None = None,
) -> VisualItemView:
    snapshot = snapshot or visual_snapshot(item)
    source = snapshot.source
    outfit = source.outfit if source.kind in ("actor", "cinematic", "analysis") else None
    scene = source.scene if source.kind in ("scene", "cinematic", "analysis") else None
    action = source.action if source.kind in ("actor", "cinematic", "analysis") else None
    auto_play_blocks = auto_play_blocks if auto_play_blocks is not None else _item_auto_play_blocks(item, snapshot)
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
        input_image_hash=item.input_image_hash,
        created_at=item.created_at,
        updated_at=item.updated_at,
        body=version_body(item),
        original_url=client_asset_url(source.original_path) if source.original_path else None,
        auto_playable=not auto_play_blocks,
    )


def _job_action_name(snapshot: dict) -> str:
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    if isinstance(source, dict) and source.get("kind") in ("actor", "cinematic"):
        action = source.get("action")
        name = action.get("name") if isinstance(action, dict) else None
        return name if isinstance(name, str) else ""
    return ""


def _job_view(job: VisualJob, queue_info: VideoQueueInfo | None = None) -> VisualJobView:
    progress = parse_job_progress(job.checkpoint)
    error = job.error
    if job.state == "failed" and progress is not None and progress.failure_reason == "quality_failed":
        error = quality_failure_message(progress.review_result) or error
    return VisualJobView(
        id=job.id,
        target_kind=job.target_kind,
        asset_kind=job.asset_kind,
        action_name=_job_action_name(job.snapshot_json),
        request_source=progress.source if progress else None,
        target_visual_id=job.target_visual_id,
        state=job.state,  # type: ignore[arg-type]
        stage=job.stage,
        error=error,
        failure_reason=progress.failure_reason if progress is not None and progress.failure_reason else None,
        video_queue=queue_info,
        result_visual_id=job.result_visual_id,
        updated_at=job.updated_at,
    )


async def job_views(db: AsyncSession, jobs: list[VisualJob]) -> list[VisualJobView]:
    requests = {
        job.id: MediaChainState.model_validate_json(
            (job.checkpoint or {}).get("video_chain_json") or "{}"
        ).active_queue_request
        for job in jobs
        if job.state in {"queued", "running", "unknown"}
    }
    info = await video_attempt_queue_info(db, {request for request in requests.values() if request})
    return [_job_view(job, info.get(requests.get(job.id))) for job in jobs]


async def job_view(db: AsyncSession, job: VisualJob) -> VisualJobView:
    return (await job_views(db, [job]))[0]


async def visual_job_detail(user_id: int, job_id: int) -> VisualJobView:
    async with SESSION_LOCAL() as db:
        return await job_view(db, await visual_job_row(db, user_id, job_id))


async def list_visuals(
    user_id: int,
    *,
    query: str | None = None,
    media_kind: VisualMediaKind | None = None,
    source: str | None = None,
    section: Literal["complete", "sprite"] | None = None,
    cursor: int | None = None,
    include_total: bool = False,
    limit: int = 50,
) -> VisualListResponse:
    """成品分页；制作工作项由 list_jobs 单独读取。"""

    async with SESSION_LOCAL() as db:
        filters = [VisualItem.user_id == user_id]
        if section is not None:
            filters.append(VisualItem.asset_kind.in_(_LIBRARY_SECTIONS[section]))
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
        if source and source.strip():
            pattern = f"%{source.strip()}%"
            snapshot = VisualVersion.snapshot_json
            filters.append(
                or_(
                    snapshot["source"]["outfit"]["name"].astext.ilike(pattern),
                    snapshot["source"]["scene"]["title"].astext.ilike(pattern),
                    snapshot["source"]["action"]["name"].astext.ilike(pattern),
                )
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
            _frozen_clip_kind().label("clip_kind"),
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
            VisualVersion.input_image_hash,
            func.coalesce(VisualVersion.snapshot_json["source"]["outfit"]["name"].astext, "").label("outfit_name"),
            func.coalesce(VisualVersion.snapshot_json["source"]["scene"]["title"].astext, "").label("scene_title"),
            VisualVersion.snapshot_json["source"]["action"]["name"].astext.label("action_name"),
            VisualVersion.snapshot_json["source"]["kind"].astext.label("source_kind"),
            VisualVersion.snapshot_json["review_result"].label("review_result"),
            VisualVersion.content_hash.label("media_content_hash"),
            VisualVersion.snapshot_json["source"].label("reference_source"),
            VisualVersion.snapshot_json["source"]["media_semantics"]["auto_selectable"]
            .as_boolean()
            .label("auto_selectable"),
            VisualVersion.body_json,
            VisualVersion.snapshot_json["source"]["presentation_approved"].as_boolean().label("presentation_approved"),
            VisualVersion.snapshot_json["source"]["original_path"].astext.label("original_path"),
            VisualVersion.snapshot_json["source"]["scene"].astext.label("scene_reference"),
            VisualVersion.snapshot_json["source"]["action"]["clip_kind"].astext.label("action_clip_kind"),
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
        analysis_states = {
            visual_id: import_analysis_state(state, stage)
            for visual_id, state, stage in (
                await db.execute(
                    select(VisualImportJob.visual_id, VisualImportJob.state, VisualImportJob.stage).where(
                        VisualImportJob.user_id == user_id,
                        VisualImportJob.visual_id.in_([row["id"] for row in rows[:limit]]),
                    )
                )
            ).all()
        }
        views = []
        for row in rows[:limit]:
            values = dict(row)
            values["analysis_state"] = analysis_states.get(values["id"])
            values["media_url"] = client_asset_url(values.pop("media_path"))
            poster = values.pop("poster_path")
            values["poster_url"] = client_asset_url(poster) if poster else None
            original_path = values.pop("original_path")
            values["original_url"] = client_asset_url(original_path) if original_path else None
            scene_reference = values.pop("scene_reference")
            action_clip_kind = values.pop("action_clip_kind")
            values.pop("source_kind")
            approved = values.pop("presentation_approved")
            values["auto_playable"] = automatic_media_ready(
                media_kind=values["media_kind"],
                asset_kind=values["asset_kind"],
                media_path=row["media_path"],
                auto_selectable=values.pop("auto_selectable"),
                clip_kind=values["clip_kind"],
                body=values.pop("body_json"),
                presentation_approved=approved,
                review_result=values.pop("review_result"),
                reference_source=values.pop("reference_source"),
                media_content_hash=values.pop("media_content_hash"),
                scene_reference=bool(scene_reference and scene_reference != "null"),
                action_clip_kind=action_clip_kind,
            )
            views.append(VisualItemView.model_validate(values))
        return VisualListResponse(
            visuals=views,
            total=total,
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            limit=limit,
        )


async def visual_version(user_id: int, visual_id: int, version_id: int) -> VisualItemView | None:
    async with SESSION_LOCAL() as db:
        row = (
            await db.execute(
                select(VisualItem, VisualVersion)
                .join(VisualVersion, VisualVersion.visual_id == VisualItem.id)
                .where(
                    VisualItem.user_id == user_id,
                    VisualItem.id == visual_id,
                    VisualVersion.user_id == user_id,
                    VisualVersion.id == version_id,
                )
            )
        ).one_or_none()
        return item_view(version_view(*row)) if row else None


async def visual_detail(user_id: int, visual_id: int) -> VisualDetailView:
    async with SESSION_LOCAL() as db:
        item = await db.scalar(select(VisualItem).where(VisualItem.id == visual_id, VisualItem.user_id == user_id))
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
                .order_by(VisualJob.id.desc())
            )
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
        auto_play_blocks = _item_auto_play_blocks(item, snapshot)
        view = item_view(item, snapshot, auto_play_blocks=auto_play_blocks)
        if analysis is not None and not view.auto_playable:
            import_blocks = _import_auto_play_blocks(analysis)
            if import_blocks:
                auto_play_blocks = _merge_import_auto_play_blocks(auto_play_blocks, import_blocks)
        return VisualDetailView(
            **view.model_dump(),
            snapshot=snapshot,
            jobs=await job_views(db, jobs),
            analysis=analysis,
            auto_play_blocks=auto_play_blocks,
        )


async def list_jobs(
    user_id: int,
    *,
    states: list[VisualJobState] | None = None,
    section: Literal["complete", "sprite"] | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> VisualJobListResponse:
    async with SESSION_LOCAL() as db:
        filters = [
            VisualJob.user_id == user_id,
            or_(VisualJob.finished_at.is_(None), VisualJob.finished_at >= utc_now() - timedelta(days=90)),
        ]
        if section is not None:
            filters.append(VisualJob.asset_kind.in_(_LIBRARY_SECTIONS[section]))
        if states:
            filters.append(VisualJob.state.in_(states))
        if cursor is not None:
            filters.append(VisualJob.id < cursor)
        fields = (
            VisualJob.id,
            VisualJob.target_kind,
            VisualJob.asset_kind,
            func.coalesce(VisualJob.snapshot_json["source"]["action"]["name"].astext, "").label("action_name"),
            VisualJob.checkpoint["source"].as_string().label("request_source"),
            VisualJob.target_visual_id,
            VisualJob.state,
            VisualJob.stage,
            VisualJob.error,
            VisualJob.checkpoint["failure_reason"].as_string().label("failure_reason"),
            VisualJob.checkpoint["review_result"].label("review_result"),
            VisualJob.checkpoint["video_chain_json"].as_string().label("video_chain_json"),
            VisualJob.result_visual_id,
            VisualJob.updated_at,
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
        requests = {
            row["id"]: MediaChainState.model_validate_json(row["video_chain_json"] or "{}").active_queue_request
            for row in rows[:limit]
            if row["state"] in {"queued", "running", "unknown"}
        }
        queue_info = await video_attempt_queue_info(db, {request for request in requests.values() if request})
        jobs = []
        for row in rows[:limit]:
            values = dict(row)
            values.pop("video_chain_json")
            values["video_queue"] = queue_info.get(requests.get(row["id"]))
            raw_review = values.pop("review_result")
            review = VisualReview.model_validate(raw_review) if raw_review is not None else None
            if values["state"] == "failed" and values["failure_reason"] == "quality_failed":
                values["error"] = quality_failure_message(review) or values["error"]
            jobs.append(VisualJobView.model_validate(values))
        return VisualJobListResponse(
            jobs=jobs,
            has_more=len(rows) > limit,
            next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            limit=limit,
        )


async def scene_reference_usable(db: AsyncSession, user_id: int, scene: SceneMediaRef | None) -> bool:
    """环境引用仍可用：文件存在且来源环境未被删除；冻结引用本身会阻止旧文件被回收。"""

    if scene is None or existing_asset(scene.path) is None:
        return False
    return scene.scene_id is None or await scene_reference_exists(db, user_id, scene.scene_id)


async def _existing_static(db: AsyncSession, user_id: int, raw: dict | None) -> tuple[StagePresentation | None, bool]:
    """冻结目标按素材核对；返回目标与是否丢弃了已删除的环境或造型。"""

    if raw is None:
        return None, False
    static = StagePresentation.model_validate(raw)
    lost = False
    if static.outfit is not None and existing_asset(static.outfit.path) is None:
        static = static.model_copy(update={"outfit": None})
        lost = True
    if static.scene is not None and not await scene_reference_usable(db, user_id, static.scene):
        static = static.model_copy(update={"scene": None})
        lost = True
    return static, lost


async def _existing_composition(
    db: AsyncSession, user_id: int, composition: StageComposition | None
) -> tuple[StageComposition | None, bool]:
    """组合里的冻结素材同样按素材核对；失效环境引用整体清空，交给重新选择。"""

    if composition is None:
        return None, False
    update: dict[str, object] = {}
    if composition.outfit_material is not None and existing_asset(composition.outfit_material.path) is None:
        update["outfit_material"] = None
    if composition.scene_material is not None and not await scene_reference_usable(
        db, user_id, composition.scene_material
    ):
        update.update({"scene_visual_id": None, "scene_version": None, "scene_material": None})
    return (composition.model_copy(update=update), True) if update else (composition, False)


async def _default_wallpaper(
    db: AsyncSession,
    user_id: int,
    state: VisualPresentation,
    base_composition: StageComposition | None,
) -> DefaultWallpaperView | None:
    """装配账户默认壁纸；成品 ID 稳定，版本随当前成品读取。"""

    if state.default_visual_id is None:
        return None
    item = await db.scalar(
        select(VisualItem).where(VisualItem.id == state.default_visual_id, VisualItem.user_id == user_id)
    )
    if item is None or item.current_version is None or item.status != "ready" or not visual_playable(item):
        return None
    snapshot = visual_snapshot(item)
    source = snapshot.source
    if (
        item.asset_kind not in {"scene", "cinematic"}
        or not source.presentation_approved
        or not visual_plays_as_loop(item, snapshot)
    ):
        return None
    scene = getattr(source, "scene", None)
    if scene is None or not existing_asset(scene.path) or not await scene_reference_usable(db, user_id, scene):
        return None
    if item.asset_kind == "cinematic":
        outfit, identity_hash = frozen_stage_reference(state)
        source_outfit = getattr(source, "outfit", None)
        source_identity_hash = getattr(source, "identity_hash", None)
        source_identity_path = getattr(source, "identity_path", None)
        if source_outfit is None or source_identity_hash is None or source_identity_path is None:
            return None
        if outfit is None or identity_hash != source_identity_hash or source_outfit.content_hash != outfit.content_hash:
            return None
        layout = (
            base_composition.layout
            if base_composition is not None and base_composition.mode == "cinematic"
            else StageCompositionLayout(layer_order=["cinematic", "effects"])
        )
        composition = StageComposition(
            mode="cinematic",
            cinematic=ActorClipRef(visual_id=item.id, version=item.version),
            outfit_material=source_outfit,
            scene_material=source.scene,
            identity_path=source_identity_path,
            identity_hash=source_identity_hash,
            layout=layout,
            selection_source="user",
        )
    else:
        base = base_composition if base_composition is not None and base_composition.mode == "layered" else None
        composition = (
            base.model_copy(
                update={
                    "scene_visual_id": item.id,
                    "scene_version": item.version,
                    "scene_material": source.scene,
                    "selection_source": "user",
                    "life_revision": None,
                    "life_activity_type": None,
                }
            )
            if base is not None
            else StageComposition(
                mode="layered",
                scene_visual_id=item.id,
                scene_version=item.version,
                scene_material=source.scene,
                layout=StageCompositionLayout(),
                selection_source="user",
            )
        )
    projected = await composition_with_media(db, user_id, composition)
    if projected is None or projected.media is None:
        return None
    media = projected.media.cinematic if projected.mode == "cinematic" else projected.media.scene
    if media is None:
        return None
    return DefaultWallpaperView(visual=item_view(item, snapshot), composition=projected)


@dataclass(frozen=True)
class VisualCreationPreferences:
    autonomous_enabled: bool = True
    target_width: int | None = None
    target_height: int | None = None


async def creation_preferences(db: AsyncSession, user_id: int) -> VisualCreationPreferences:
    row = (
        await db.execute(
            select(
                VisualPresentation.autonomous_enabled, VisualPresentation.target_width, VisualPresentation.target_height
            ).where(VisualPresentation.user_id == user_id)
        )
    ).one_or_none()
    return VisualCreationPreferences(*row) if row else VisualCreationPreferences()


@dataclass(frozen=True)
class StageRecoveryView:
    state_revision: int = 0
    static_presentation: StagePresentation | None = None
    composition: StageComposition | None = None
    has_idle_actor: bool = False


async def _stage_materials(
    db: AsyncSession, row: VisualPresentation
) -> tuple[StagePresentation | None, StageComposition | None]:
    static, lost_static = await _existing_static(db, row.user_id, row.pending_static_json or row.current_static_json)
    composition, lost_composition = await _existing_composition(db, row.user_id, stage_composition(row))
    presence = get_presentation_snapshot(row.user_id)
    # 已呈现的目标丢失素材时唤醒恢复；准备中的目标由原流程继续。
    if (
        lost_static
        or lost_composition
        or (
            static is not None
            and presence is not None
            and static.receipt_kind == ("window_static" if presence.mode == "window" else "desktop_static")
            and static.error is None
            and (static.outfit is None or static.scene is None)
        )
    ):
        request_stage_recovery(row.user_id)
    return static, composition


async def stage_recovery_view(db: AsyncSession, user_id: int) -> StageRecoveryView:
    row = await db.get(VisualPresentation, user_id)
    if row is None:
        return StageRecoveryView()
    static, composition = await _stage_materials(db, row)
    return StageRecoveryView(
        state_revision=row.state_revision,
        static_presentation=static,
        composition=await composition_with_media(db, user_id, composition),
        has_idle_actor=await _has_idle_actor(db, row),
    )


async def presentation_state(user_id: int) -> VisualPresentationView:
    event_loop = asyncio.get_running_loop()
    preference_deadline = event_loop.time() + _STAGE_PREFERENCE_WINDOW_SECONDS
    async with SESSION_LOCAL() as db:
        row = await db.get(VisualPresentation, user_id)
        now = utc_now()
        rhythm = await read_rhythm(db, user_id, now=now)
        if row is None:
            return VisualPresentationView(rhythm=rhythm)
        presence = get_presentation_snapshot(user_id)
        frame = presented_frame(row, presence)
        shown = frame if isinstance(frame, PresentedVisual) else None
        loop = presented_item(row, loop=True)
        static, composition = await _stage_materials(db, row)
        scene = (static.scene if static else None) or (composition.scene_material if composition else None)
        intent = StageIntent.model_validate(row.intent_json) if row.intent_json else None
        life = await get_life_state_view(db, user_id)
        if life is not None and (intent is None or life.presentation_revision > intent.life_revision):
            intent = project_stage_intent(life, await active_pursuit_title(db, user_id, life.active_pursuit_id))
        current = (shown.id, shown.version) if shown is not None else None
        pending = active_pending_command(row)
        retained = {key for key in (current, (pending.visual_id, pending.visual_version) if pending else None) if key}
        wake_from = shown if rhythm.wake is not None and rhythm.wake.expires_at > now else None
        catalog = await ambient_catalog(db, row, intent=intent, rhythm=rhythm, wake_from=wake_from, retained=retained)
        plan = await _stage_playback_plan(db, user_id, catalog, scene, intent, rhythm, now, shown=shown)
        view = VisualPresentationView(
            composition=await composition_with_media(db, user_id, composition),
            intent=intent,
            rhythm=rhythm,
            stage_plan=plan,
            current_visual=item_view(shown) if shown is not None else None,
            loop_visual=item_view(loop) if loop is not None and loop.user_id == user_id else None,
            default_wallpaper=await _default_wallpaper(db, user_id, row, composition),
            autonomous_enabled=row.autonomous_enabled,
            target_width=row.target_width,
            target_height=row.target_height,
            presentation_epoch=presence.revision if presence is not None else row.presentation_epoch,
            state_revision=row.state_revision,
            pending_play=pending,
            static_presentation=static,
        )
    if view.stage_plan is not None and current is not None:
        view.stage_plan.cinematic_variants.sort(key=lambda entry: (entry.visual_id, entry.version) != current)
    if view.stage_plan is not None and decision_enabled() and event_loop.time() < preference_deadline:
        preference_window = asyncio.timeout_at(preference_deadline)
        try:
            async with preference_window:
                view.stage_plan = await _prefer_stage_plan(
                    user_id,
                    view.stage_plan.model_copy(),
                    catalog,
                    scene,
                    intent,
                    rhythm,
                    now,
                    current=current,
                    shown=shown,
                )
        except TimeoutError:
            if not preference_window.expired():
                raise
    view.ambient_catalog = _presentation_catalog(catalog, view.stage_plan, intent, rhythm, retained)
    return view


def _presentation_catalog(
    catalog: list[StageMediaCandidate],
    stage: StagePlaybackPlan | None,
    intent: StageIntent | None,
    rhythm: RhythmView,
    retained: set[tuple[int, int]],
) -> list[AmbientCatalogEntry]:
    actors = [candidate.entry for candidate in catalog if candidate.entry.asset_kind == "actor"]
    if stage is None or stage.scene is None:
        return actors
    scene_hash = stage.scene.content_hash
    asleep = rhythm.state == "asleep"
    scene_tags = set(intent.scene_tags if intent else ())
    reactions = [
        entry
        for candidate in catalog
        if (entry := candidate.entry).asset_kind == "cinematic"
        and entry.clip_kind == "once"
        and entry.performance_role == "reaction"
        and entry.media_semantics is not None
        and not {"sleeping", "waking"}.intersection(entry.media_semantics.semantic_tags)
        and any(trigger in entry.allowed_triggers for trigger in _STAGE_REACTION_TRIGGERS)
        and entry.scene_content_hash == scene_hash
        and phase_compatible(entry.day_phase, stage.day_phase, asleep=asleep)
        and compatible_activity(entry, stage.activity_type, scene_tags, required=False)
    ]
    # 先保留已受理和已开播的版本，以及每个受支持触发器的首个合格候选。
    reserved = {
        (entry.visual_id, entry.version): entry for entry in reactions if (entry.visual_id, entry.version) in retained
    }
    for trigger in _STAGE_REACTION_TRIGGERS:
        if entry := next((entry for entry in reactions if trigger in entry.allowed_triggers), None):
            reserved.setdefault((entry.visual_id, entry.version), entry)
    remaining = [entry for entry in reactions if (entry.visual_id, entry.version) not in reserved]
    return [*actors, *[*reserved.values(), *remaining][:_STAGE_REACTION_LIMIT]]


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
            .where(VisualItem.id == visual_id, VisualItem.user_id == user_id, VisualVersion.number == version)
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
        day_phase=(source.get("day_phase") or (source.get("media_semantics") or {}).get("day_phase")),
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
    if source is not None and carries_character(source):
        return (source.outfit if outfit is None else outfit), source.identity_hash
    return outfit, composition.identity_hash if composition else None


def _stage_catalog_priority(candidate: StageMediaCandidate, intent: StageIntent | None) -> int:
    entry = candidate.entry
    actual = entry.media_semantics
    if actual is not None and {"sleeping", "waking"}.intersection(actual.semantic_tags):
        return 0
    if entry.preset_key in {"idle", "look", "smile", "stretch", "relax"}:
        return 1
    if intent is not None and actual is not None and actual.activity_type == intent.activity_type:
        return 2
    return 3


async def ambient_catalog(
    db: AsyncSession,
    state: VisualPresentation,
    *,
    intent: StageIntent | None,
    rhythm: RhythmView,
    wake_from: PresentedVisual | None = None,
    retained: set[tuple[int, int]] | None = None,
    limit: int = 24,
) -> list[StageMediaCandidate]:
    """常驻、互动与整景分别限额，保留同一动作的不同合格成品。"""

    outfit, identity_hash = frozen_stage_reference(state)
    if outfit is None or identity_hash is None:
        return []
    preset_keys = await action_preset_keys(db, state.user_id)
    asset_kind = _frozen_asset_kind()
    clip_kind = _frozen_clip_kind()
    query = (
        select(
            VisualItem.id,
            asset_kind.label("asset_kind"),
            clip_kind.label("clip_kind"),
            VisualVersion.action_id,
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
            VisualVersion.snapshot_json["review_result"].label("review_result"),
            VisualVersion.content_hash.label("media_content_hash"),
        )
        .select_from(VisualItem)
        .join(VisualVersion, VisualVersion.visual_id == VisualItem.id)
        .where(
            VisualItem.user_id == state.user_id,
            VisualItem.status == "ready",
            asset_kind.in_(("actor", "cinematic")),
            # 完整场景的静态图片同样是可持续画面；actor 仍只能以视频进入目录。
            or_(VisualItem.media_kind == "video", asset_kind == "cinematic"),
            VisualVersion.snapshot_json["source"]["identity_hash"].astext == identity_hash,
            VisualVersion.snapshot_json["source"]["outfit"]["content_hash"].astext == outfit.content_hash,
        )
    )
    buckets: dict[tuple, list[StageMediaCandidate]] = {
        ("actor", "loop"): [],
        ("actor", "once"): [],
        ("actor", "sleeping"): [],
        ("actor", "waking"): [],
    }
    selected_items: set[int] = set()
    selected_content: set[str] = set()
    preset_variants: dict[tuple[tuple, str], int] = {}
    retained_entries: list[StageMediaCandidate] = []
    activity = stage_activity(intent, rhythm)
    scene_tags = set(intent.scene_tags if intent else ())
    sorting = [VisualVersion.id.desc()]
    if (
        wake_from is not None
        and (sleep := _displayed_sleep_source(wake_from)) is not None
        and (first_frame := sleep.delivery_first_frame) is not None
    ):
        frozen_source = VisualVersion.snapshot_json["source"]
        opening = frozen_source["opening_frame"]
        # 当前安睡版本的精确配对先于同库其它起身片段占用限额。
        exact_wake = and_(
            asset_kind == "cinematic",
            clip_kind == "once",
            frozen_source["media_semantics"]["semantic_tags"].contains(["waking"]),
            frozen_source["scene"]["content_hash"].astext == sleep.scene.content_hash,
            opening["visual_id"].astext == str(wake_from.id),
            opening["version"].astext == str(wake_from.version),
            opening["content_hash"].astext == first_frame.content_hash,
        )
        sorting.insert(0, case((exact_wake, 0), else_=1).asc())
    rows = await db.stream(query.order_by(*sorting), execution_options={"yield_per": 32})
    try:
        async for row in rows:
            source = row.source or {}
            semantics = source.get("media_semantics") or {}
            tags = semantics.get("semantic_tags") or []
            category = row.clip_kind if row.asset_kind == "actor" else None
            keep_version = False
            interaction = ()
            if "sleeping" in tags and row.clip_kind != "once":
                category = "sleeping"
            elif row.asset_kind == "actor" and row.clip_kind == "once" and "waking" in tags:
                category = "waking"
            bucket_key = (row.asset_kind, category)
            if row.asset_kind == "cinematic":
                frozen_action = source.get("action") or {}
                if row.clip_kind == "once":
                    keep_version = retained is not None and (row.id, row.number) in retained
                    if "waking" in tags:
                        category = "waking"
                    elif frozen_action.get("performance_role") == "reaction":
                        allowed = frozen_action.get("allowed_triggers") or ()
                        interaction = tuple(trigger for trigger in _STAGE_REACTION_TRIGGERS if trigger in allowed)
                    if not (keep_version or category == "waking" or interaction):
                        continue
                bucket_key = (
                    "cinematic",
                    category or row.clip_kind,
                    row.media_kind,
                    semantics.get("activity_type"),
                    semantics.get("day_phase"),
                    (source.get("scene") or {}).get("content_hash"),
                    _subtle_motion(frozen_action.get("motion_intensity"), semantics.get("motion_intensity")),
                    interaction,
                )
            elif bucket_key not in buckets:
                continue
            bucket = buckets.get(bucket_key, [])
            preset = preset_keys.get(row.action_id)
            if not keep_version and (
                row.id in selected_items
                or row.media_content_hash in selected_content
                or (row.asset_kind == "cinematic" and len(bucket) >= limit)
            ):
                continue
            if not automatic_media_ready(
                media_kind=row.media_kind,
                asset_kind=row.asset_kind,
                media_path=row.media_path,
                auto_selectable=semantics.get("auto_selectable"),
                clip_kind=row.clip_kind,
                body=row.body_json,
                presentation_approved=source.get("presentation_approved"),
                review_result=row.review_result,
                reference_source=source,
                media_content_hash=row.media_content_hash,
                scene_reference=bool(source.get("scene")),
                action_clip_kind=(source.get("action") or {}).get("clip_kind"),
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
            if action is None and (row.media_kind != "image" or not semantics):
                continue
            if not (keep_version or interaction):
                selected_items.add(row.id)
            if not keep_version and preset and preset_variants.get((bucket_key, preset), 0) >= 6:
                continue
            scene_raw = source.get("scene")
            scene = SceneMediaRef.model_validate(scene_raw) if scene_raw else None
            entry = AmbientCatalogEntry(
                visual_id=row.id,
                version=row.number,
                media_kind=row.media_kind,
                asset_kind=row.asset_kind,
                clip_kind=row.clip_kind,
                media_url=client_asset_url(row.media_path),
                poster_url=client_asset_url(row.poster_path) if row.poster_path else None,
                width=row.width,
                height=row.height,
                duration_ms=row.duration_ms,
                description=row.description,
                body=ActorClipBody.model_validate(row.body_json) if row.body_json else None,
                day_phase=semantics.get("day_phase"),
                scene_content_hash=scene.content_hash if scene else None,
                action_id=row.action_id,
                action_name=action.name if action else "",
                preset_key=preset,
                semantic_tags=action.semantic_tags if action else semantics.get("semantic_tags", []),
                performance_role=action.performance_role if action and row.media_kind == "video" else "sustained",
                motion_intensity=action.motion_intensity if action else semantics.get("motion_intensity", "subtle"),
                interruptible=action.interruptible if action else True,
                media_semantics=source.get("media_semantics"),
                allowed_triggers=action.allowed_triggers
                if action and row.media_kind == "video"
                else ["manual", "periodic"],
                use_when=action.use_when if action else [],
                avoid_when=action.avoid_when if action else [],
            )
            opening = source.get("opening_frame") or {}
            opening_ref = ActorClipRef(visual_id=opening["visual_id"], version=opening["version"]) if opening else None
            candidate = StageMediaCandidate(entry, scene, opening.get("content_hash"), opening_ref)
            if keep_version:
                retained_entries.append(candidate)
                continue
            if interaction and not compatible_activity(entry, activity, scene_tags, required=False):
                continue
            selected_items.add(row.id)
            selected_content.add(row.media_content_hash)
            bucket.append(candidate)
            buckets[bucket_key] = bucket
            if preset:
                key = (bucket_key, preset)
                preset_variants[key] = preset_variants.get(key, 0) + 1
            if len(bucket) > limit:
                # 精灵候选先保留待机与当前活动优先级，再按分组容量淘汰。
                bucket.sort(key=lambda candidate: _stage_catalog_priority(candidate, intent))
                bucket.pop()
    finally:
        await rows.close()
    return retained_entries + [
        candidate
        for entries in buckets.values()
        for candidate in sorted(entries, key=lambda candidate: _stage_catalog_priority(candidate, intent))
    ]


async def _has_idle_actor(db: AsyncSession, state: VisualPresentation) -> bool:
    """当前冻结造型是否有可自动播放的待机成品。"""

    outfit, identity_hash = frozen_stage_reference(state)
    if outfit is None or identity_hash is None:
        return False
    idle = await action_by_preset_key(db, state.user_id, "idle")
    if idle is None:
        return False
    versions = await db.scalars(
        select(VisualVersion)
        .join(VisualItem, VisualItem.id == VisualVersion.visual_id)
        .where(
            VisualItem.user_id == state.user_id,
            _frozen_asset_kind() == "actor",
            VisualItem.status == "ready",
            VisualItem.media_kind == "video",
            VisualVersion.action_id == idle.id,
            _frozen_clip_kind() == "loop",
            VisualVersion.snapshot_json["source"]["identity_hash"].astext == identity_hash,
            VisualVersion.snapshot_json["source"]["outfit"]["content_hash"].astext == outfit.content_hash,
        )
        .order_by(VisualVersion.id.desc())
    )
    for version in versions:
        snapshot = VisualSnapshot.model_validate(version.snapshot_json)
        frozen = snapshot.source
        if (
            isinstance(frozen, (ActorClipSource, AnalyzingVisualSource))
            and (frozen.kind != "analysis" or (frozen.asset_kind == "actor" and frozen.presentation_approved))
            and frozen.outfit is not None
            and frozen.action is not None
            and frozen.identity_hash == identity_hash
            and frozen.outfit.content_hash == outfit.content_hash
            and (frozen.clip_kind if frozen.kind == "analysis" else frozen.action.clip_kind) == "loop"
            and automatic_media_ready(
                media_kind="video",
                asset_kind="actor",
                media_path=version.media_path,
                auto_selectable=_frozen_auto_selectable(frozen),
                clip_kind="loop",
                body=version.body_json,
                presentation_approved=frozen.presentation_approved,
                review_result=snapshot.review_result,
                reference_source=frozen.model_dump(mode="json"),
                media_content_hash=version.content_hash,
                action_clip_kind=frozen.action.clip_kind,
            )
        ):
            return True
    return False


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
            VisualVersion.snapshot_json["source"].label("source"),
            VisualVersion.snapshot_json["review_result"].label("review_result"),
            VisualVersion.content_hash.label("media_content_hash"),
        )
        .select_from(VisualItem)
        .join(VisualVersion, VisualVersion.id == VisualItem.current_version_id)
        .where(
            VisualItem.user_id == user_id,
            VisualItem.status == "ready",
            VisualItem.media_kind == "video",
            VisualItem.asset_kind == "scene",
            _frozen_clip_kind() == "loop",
            VisualVersion.snapshot_json["source"]["scene"]["content_hash"].astext == scene.content_hash,
            VisualVersion.snapshot_json["source"]["presentation_approved"].as_boolean().is_(True),
        )
        .order_by(VisualVersion.id.desc()),
        execution_options={"yield_per": 32},
    )
    entries: list[StageLayerMedia] = []
    phases: set[str | None] = set()
    try:
        async for row in rows:
            if row.day_phase in phases or not automatic_media_ready(
                media_kind="video",
                asset_kind="scene",
                media_path=row.media_path,
                auto_selectable=None,
                clip_kind="loop",
                body=None,
                presentation_approved=(row.source or {}).get("presentation_approved"),
                review_result=row.review_result,
                reference_source=row.source,
                media_content_hash=row.media_content_hash,
                scene_reference=True,
            ):
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


def _subtle_motion(declared: str | None, observed: str | None) -> bool:
    return declared == observed == "subtle"


async def _usable_cinematics(
    db: AsyncSession, user_id: int, candidates: list[StageMediaCandidate]
) -> list[StageMediaCandidate]:
    approved: list[tuple[int, StageMediaCandidate]] = []
    modes: dict[str, tuple[int, StageMediaCandidate]] = {}
    known: dict[tuple[int | None, str], bool] = {}
    scene_hash: str | None = None
    for index, candidate in enumerate(candidates):
        scene = candidate.scene
        if scene is None or (scene_hash is not None and scene.content_hash != scene_hash):
            continue
        entry = candidate.entry
        actual = entry.media_semantics
        mode = None
        if entry.media_kind == "image":
            mode = "image"
        elif actual and _subtle_motion(entry.motion_intensity, actual.motion_intensity):
            mode = "subtle"
        if len(approved) == _STAGE_CINEMATIC_LIMIT and (mode is None or mode in modes):
            continue
        key = (scene.scene_id, scene.path)
        if key not in known:
            known[key] = await scene_reference_usable(db, user_id, scene)
        if not known[key]:
            continue
        if scene_hash is None:
            scene_hash = scene.content_hash
        if len(approved) < _STAGE_CINEMATIC_LIMIT:
            approved.append((index, candidate))
        if mode is not None:
            modes.setdefault(mode, (index, candidate))
        if len(approved) == _STAGE_CINEMATIC_LIMIT and len(modes) == 2:
            break
    # 为关闭动效和轻量档保留候选，其余名额沿用原情境排序。
    reserved = {index for index, _ in modes.values()}
    alternatives = [pair for pair in approved if pair[0] not in reserved]
    selected = [*modes.values(), *alternatives][:_STAGE_CINEMATIC_LIMIT]
    return [candidate for _, candidate in sorted(selected)]


async def _cinematic_scene_options(
    db: AsyncSession, user_id: int, candidates: list[StageMediaCandidate]
) -> list[SceneMediaRef]:
    scenes: dict[int | str, SceneMediaRef] = {}
    checked: set[tuple[int | None, str]] = set()
    for candidate in candidates:
        scene = candidate.scene
        if scene is None or (key := scene.scene_id or scene.path) in scenes:
            continue
        ref = (scene.scene_id, scene.path)
        if ref in checked:
            continue
        checked.add(ref)
        if await scene_reference_usable(db, user_id, scene):
            scenes[key] = scene
        if len(scenes) == _STAGE_CINEMATIC_LIMIT:
            break
    return list(scenes.values())


def _media_at_phase(catalog: list[StageLayerMedia], phase: DayPhase) -> StageLayerMedia | None:
    return next((entry for entry in catalog if entry.day_phase == phase), None) or next(
        (entry for entry in catalog if entry.day_phase is None), None
    )


async def _stage_playback_plan(
    db: AsyncSession,
    user_id: int,
    catalog: list[StageMediaCandidate],
    base: SceneMediaRef | None,
    intent: StageIntent | None,
    rhythm: RhythmView,
    now: datetime,
    *,
    scene_alias: SceneMediaRef | None = None,
    shown: PresentedVisual | None = None,
) -> StagePlaybackPlan:
    phase, next_phase = stage_day_phase(now, rhythm.timezone)
    activity = stage_activity(intent, rhythm)
    asleep = rhythm.state == "asleep"
    wake = rhythm.wake if rhythm.wake is not None and rhythm.wake.expires_at > now else None
    scene_title = (
        scene_alias.title.strip().casefold()
        if scene_alias is not None
        else intent.scene_title.strip().casefold()
        if intent is not None
        else ""
    )
    resting_scene = None
    resting_cinematics: list[StageMediaCandidate] = []
    if asleep or wake is not None:
        # 过渡继续使用安睡时的环境选择；配对动作的首帧来自真实睡眠循环。
        rest_phase = (
            stage_day_phase(wake.occurred_at - timedelta(microseconds=1), rhythm.timezone)[0]
            if wake is not None
            else phase
        )
        resting_cinematics = await _usable_cinematics(
            db, user_id, cinematic_options(catalog, "resting", rest_phase, None, asleep=True)
        )
        resting_scene = (resting_cinematics[0].scene if resting_cinematics else None) or base

    if asleep:
        scene = resting_scene
        cinematics = resting_cinematics
    else:
        matching_intent = (
            intent.model_copy(update={"scene_title": scene_alias.title}) if intent and scene_alias else intent
        )
        named = scene_alias or (
            base if scene_matches(base, scene_title) else await read_stage_scene(db, user_id, scene_title)
        )
        scene = named or base
        compatible = cinematic_options(catalog, activity, phase, matching_intent, asleep=False)
        if scene is not None:
            compatible = [
                candidate
                for candidate in compatible
                if candidate.scene is not None and candidate.scene.content_hash == scene.content_hash
            ]
        cinematics = await _usable_cinematics(db, user_id, compatible)
        if scene is None and cinematics:
            scene = cinematics[0].scene
    if not cinematics and not asleep and not scene_title and scene is not None and activity != "idle":
        idle = cinematic_options(catalog, "idle", phase, None, asleep=False)
        cinematics = await _usable_cinematics(
            db, user_id, [item for item in idle if item.scene and item.scene.content_hash == scene.content_hash]
        )
    tags = set(intent.scene_tags) if intent is not None and scene_matches(scene, scene_title) else set()
    if asleep and resting_cinematics and resting_cinematics[0].entry.media_semantics is not None:
        tags = set(resting_cinematics[0].entry.media_semantics.scene_tags)

    actors, fallback = actor_options(catalog, activity, tags, intent, asleep=asleep)
    wake_actor = None
    if asleep or wake is not None:
        sleep_tags = (
            set(intent.scene_tags) if intent is not None and scene_matches(resting_scene, scene_title) else set()
        )
        if resting_cinematics and resting_cinematics[0].entry.media_semantics is not None:
            sleep_tags = set(resting_cinematics[0].entry.media_semantics.scene_tags)
        sleep_actors = actors if asleep else actor_options(catalog, "resting", sleep_tags, None, asleep=True)[0]
        pair = paired_wake(catalog, sleep_actors, sleep_tags)
        if pair is not None:
            sleep, wake_actor = pair
            if asleep:
                actors = [sleep]
    scene_catalog = await ambient_scene_catalog(db, user_id, scene)
    wake_catalog = scene_catalog
    if wake is not None and resting_scene != scene:
        wake_catalog = await ambient_scene_catalog(db, user_id, resting_scene)
    pose = next((entry.body.first_frame_path for entry in (actors or fallback) if entry.body is not None), None)
    plan = StagePlaybackPlan(
        activity_type=activity,
        life_revision=intent.life_revision if intent is not None else 0,
        day_phase=phase,
        next_phase_at=next_phase,
        scene=scene,
        scene_media=_media_at_phase(scene_catalog, phase),
        actor_variants=actors,
        fallback_actor_variants=fallback,
        cinematic_variants=[candidate.entry for candidate in cinematics],
        actor_still_url=pose,
        wake_actor=wake_actor if wake is not None else None,
        wake_scene=resting_scene if wake is not None else None,
        wake_scene_media=_media_at_phase(wake_catalog, phase) if wake is not None else None,
    )
    if wake is not None and shown is not None:
        await _match_displayed_cinematic_wake(db, user_id, plan, catalog, shown)
    return plan


def _displayed_sleep_source(shown: PresentedVisual) -> CinematicSource | None:
    if shown.asset_kind != "cinematic" or shown.receipt_kind != "video":
        return None
    source = visual_snapshot(shown).source
    if (
        source.kind != "cinematic"
        or source.action.clip_kind != "loop"
        or source.media_semantics is None
        or "sleeping" not in source.media_semantics.semantic_tags
    ):
        return None
    return source


async def _match_displayed_cinematic_wake(
    db: AsyncSession,
    user_id: int,
    plan: StagePlaybackPlan,
    catalog: list[StageMediaCandidate],
    shown: PresentedVisual,
) -> None:
    source = _displayed_sleep_source(shown)
    if (
        source is None
        or (first_frame := source.delivery_first_frame) is None
        or existing_asset(first_frame.path) is None
        or not await scene_reference_usable(db, user_id, source.scene)
    ):
        return
    sleep = ActorClipRef(visual_id=shown.id, version=shown.version)
    wake = paired_cinematic_wake(catalog, sleep, source.scene, first_frame.content_hash)
    if wake is not None:
        plan.wake_cinematic = wake
        plan.wake_cinematic_from = sleep


async def _prefer_stage_plan(
    user_id: int,
    plan: StagePlaybackPlan,
    catalog: list[StageMediaCandidate],
    base: SceneMediaRef | None,
    intent: StageIntent | None,
    rhythm: RhythmView,
    now: datetime,
    *,
    current: tuple[int, int] | None = None,
    shown: PresentedVisual | None = None,
) -> StagePlaybackPlan:
    """语义决策仅排列已经可播的候选，作息过渡继续使用真实首帧配对。"""
    activity, phase = plan.activity_type, plan.day_phase
    title = intent.scene_title.strip() if intent is not None else ""
    if rhythm.state != "asleep" and title and not scene_matches(plan.scene, title.casefold()):
        alternatives = cinematic_options(catalog, activity, phase, intent, asleep=False, match_title=False)
        async with SESSION_LOCAL() as db:
            aliases = await _cinematic_scene_options(db, user_id, alternatives)
        if alias := await stage_scene_alias(user_id, title, aliases):
            async with SESSION_LOCAL() as db:
                if await scene_reference_usable(db, user_id, alias):
                    plan = await _stage_playback_plan(
                        db, user_id, catalog, base, intent, rhythm, now, scene_alias=alias, shown=shown
                    )

    # 完整影像只在已选择的同一环境内重排，避免下一段活动换到相似但不同的地点。
    matching_keys = {
        (candidate.entry.visual_id, candidate.entry.version)
        for candidate in catalog
        if candidate.scene is not None
        and plan.scene is not None
        and candidate.scene.content_hash == plan.scene.content_hash
    }
    matching = [entry for entry in plan.cinematic_variants if (entry.visual_id, entry.version) in matching_keys]
    if len(matching) > 1:
        async with SESSION_LOCAL() as db:
            persona = await planning_persona(db, user_id)
        ranked = iter(
            await rank_stage_variants(
                user_id,
                "cinematic",
                matching,
                activity=activity,
                phase=phase,
                scene=plan.scene,
                intent=intent,
                personality=(persona.definition.get("personality") or "") if persona else "",
                current=current,
            )
        )
        plan.cinematic_variants = [next(ranked) if entry in matching else entry for entry in plan.cinematic_variants]
    if rhythm.state == "asleep" or plan.wake_actor is not None:
        return plan
    use_fallback = not plan.actor_variants
    entries = plan.fallback_actor_variants if use_fallback else plan.actor_variants
    entries = await rank_stage_variants(
        user_id, "actor", entries, activity=activity, phase=phase, scene=plan.scene, intent=intent
    )
    if use_fallback:
        plan.fallback_actor_variants = entries
    else:
        plan.actor_variants = entries
    plan.actor_still_url = next((entry.body.first_frame_path for entry in entries if entry.body is not None), None)
    return plan


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


async def _presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        presence = get_presentation_snapshot(user_id)
        if presence is None or presence.surface == "none":
            return ActualPresentationSnapshot()
        frame = presented_frame(row, presence)
        if isinstance(frame, PresentedScene):
            scene = frame.scene
            if scene is None or not await scene_reference_usable(db, user_id, scene):
                return ActualPresentationSnapshot()
            return ActualPresentationSnapshot(
                asset_kind="scene",
                media_kind="image",
                scene_path=scene.path,
                scene_hash=scene.content_hash,
                scene_description=scene.description,
                ready=True,
            )
        item = frame
        if row and row.current_static_json and item is None:
            static = StagePresentation.model_validate(row.current_static_json)
            if static.presentation_epoch != presence.revision:
                return ActualPresentationSnapshot()
            outfit = static.outfit if presence.surface == "sprite" else None
            scene = static.scene if presence.surface != "sprite" else None
            outfit_path = existing_asset(outfit.path) if outfit is not None else None
            scene_path = scene.path if scene is not None and await scene_reference_usable(db, user_id, scene) else None
            return ActualPresentationSnapshot(
                outfit_path=outfit_path,
                outfit_hash=outfit.content_hash if outfit and outfit_path else None,
                outfit_description=outfit.description if outfit else "",
                scene_path=scene_path,
                scene_hash=scene.content_hash if scene and scene_path else None,
                scene_description=scene.description if scene else "",
                identity_path=existing_asset(static.identity_path),
                identity_hash=static.identity_hash,
                ready=static.receipt_kind is not None
                and static.receipt_kind == ("window_static" if presence.mode == "window" else "desktop_static")
                and (outfit_path is not None or scene_path is not None),
            )
        if item is None or item.user_id != user_id:
            return ActualPresentationSnapshot()
        snapshot = visual_snapshot(item)
        source = snapshot.source
        show_full_visual = item.receipt_kind != "window_static"
        if item.media_kind == "image":
            visual_path = existing_asset(item.media_path)
        elif item.receipt_kind == "image":
            visual_path = existing_asset(item.poster_path)
        elif item.asset_kind == "cinematic":
            frame = source.delivery_first_frame if source.kind == "cinematic" else None
            visual_path = (existing_asset(frame.path) if frame else None) or existing_asset(item.poster_path)
        else:
            visual_path = existing_asset(item.input_image_path or item.poster_path)
        composition = stage_composition(row) if row else None
        character_source = carries_character(source)
        environment_source = carries_scene(source)
        outfit = source.outfit if character_source else None
        scene = source.scene if environment_source else None
        outfit_path = existing_asset(outfit.path) if outfit else None
        scene_path = scene.path if await scene_reference_usable(db, user_id, scene) else None
        identity_path = existing_asset(
            source.identity_path if character_source else composition.identity_path if composition else None
        )
        outfit_description = (item.outfit_description if show_full_visual else "") or (
            outfit.description if outfit else ""
        )
        return ActualPresentationSnapshot(
            visual_id=item.id,
            media_kind="image" if item.receipt_kind == "image" else item.media_kind,
            asset_kind=item.asset_kind,
            visual_version=item.version,
            visual_description=item.description
            if show_full_visual and (item.media_kind == "image" or item.receipt_kind == "video")
            else "",
            visual_path=visual_path if show_full_visual else None,
            rest_transition=displayed_rest_transition(item, snapshot),
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
            ready=show_full_visual
            and (item.receipt_kind == item.media_kind or item.receipt_kind == "image")
            and visual_playable(item)
            and (item.media_kind != "video" or item.receipt_kind != "image" or visual_path is not None),
        )


async def actual_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """实际呈现快照：冻结造型与环境资料加成品描述；素材缺失的字段为空。"""

    return await _presentation_snapshot(user_id)


async def prompt_presentation_snapshot(user_id: int) -> ActualPresentationSnapshot:
    """供伙伴判断和媒体策划使用的模式无关快照。"""

    return await _presentation_snapshot(user_id)


async def visual_play_catalog(user_id: int, trigger: str) -> list[VisualPlayOption]:
    """按动作触发条件过滤可播影像；播放方式与条件来自冻结输入的动作引用。"""

    async with SESSION_LOCAL() as db:
        presence = get_presentation_snapshot(user_id)
        if presence is None or presence.surface == "none":
            return []
        state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        if state is not None and await full_stage_rest_in_progress(db, state, presence):
            return []
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
                VisualVersion.snapshot_json["review_result"].label("review_result"),
                VisualVersion.content_hash.label("media_content_hash"),
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
            if not visual_surface_accepts(presence.surface, row.asset_kind):
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
            semantics = source.get("media_semantics") or {}
            if row.asset_kind == "cinematic" and "waking" in (semantics.get("semantic_tags") or ()):
                continue
            action = (
                ActionMaterialRef.model_validate(source["action"])
                if source["kind"] in ("actor", "cinematic", "analysis") and source.get("action")
                else None
            )
            if not automatic_media_ready(
                media_kind=row.media_kind,
                asset_kind=row.asset_kind,
                media_path=row.media_path,
                auto_selectable=semantics.get("auto_selectable"),
                clip_kind=row.clip_kind,
                body=row.body_json,
                presentation_approved=source.get("presentation_approved"),
                review_result=row.review_result,
                reference_source=source,
                media_content_hash=row.media_content_hash,
                scene_reference=bool(source.get("scene")),
                action_clip_kind=action.clip_kind if action else None,
            ):
                continue
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
    user_id: int, action_id: int, *, cursor: int | None = None, limit: int = 30
) -> ActionMediaListResponse:
    """动作详情的媒体实现清单：该动作已制作的可播成品投影。

    `stage_ready` 标记该实现与当前舞台冻结身份、造型一致且文件完整——用户据此
    区分“这个动作现在能直接播”与“属于其他造型的历史版本”。
    """

    async with SESSION_LOCAL() as db:
        state = await db.scalar(select(VisualPresentation).where(VisualPresentation.user_id == user_id))
        outfit, identity_hash = frozen_stage_reference(state) if state is not None else (None, None)
        filters = [VisualItem.user_id == user_id, VisualItem.action_id == action_id, VisualItem.status == "ready"]
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
            VisualVersion.snapshot_json["review_result"].label("review_result"),
            VisualVersion.content_hash.label("media_content_hash"),
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
