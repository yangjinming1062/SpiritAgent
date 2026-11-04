"""用户影像分析、分类型查重、受控提取和呈现复核的跨业务编排。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from features.action_library.import_materials import action_is_imported, undo_imported_action
from features.action_library.queries import action_detail, list_actions
from features.action_library.review import review_action
from features.action_library.service import create_action, retry_review, update_action
from features.appearance.avatar_service import FULLBODY_SIZE
from features.appearance.identity import load_visual_identity
from features.appearance.import_materials import accept_imported_outfit, undo_imported_outfit
from features.appearance.queries import avatar_snapshot, ready_outfit_snapshot, ready_outfit_snapshots
from features.assets.lifecycle import cleanup_asset_directories_after_task
from features.configuration.settings.values import get_user_setting
from features.life.material_references import material_has_references
from features.media.character_images import ImageChainState, generate_reviewed_images, generate_scene_images
from features.media.image_generation import ImageGenerationError, ImageResultDownloadError, ImageReviewUnavailableError
from features.scenes.import_materials import accept_imported_scene, undo_imported_scene
from features.scenes.queries import scene_snapshot
from features.scenes.snapshots import list_scene_snapshots
from features.visuals.import_jobs import (
    ImportCatalogCandidate,
    ImportFrameRef,
    ImportWork,
    claim_import_job,
    command_import_job,
    ensure_import_job,
    fail_import_work,
    guard_import_work,
    recover_import_jobs,
    save_import_progress,
    take_import_operation,
)
from features.visuals.import_media import (
    extract_region,
    prepare_imported_actor,
    retrieval_score,
    sample_import,
    visual_vector,
    visual_vector_bytes,
)
from features.visuals.import_operations import extraction_command
from features.visuals.import_prompts import IMPORT_OUTFIT_GENERATION, IMPORT_SCENE_GENERATION
from features.visuals.import_publication import apply_import_result, complete_import_analysis
from features.visuals.import_reasoning import (
    ImportQualityVerdict,
    design_import_pose,
    match_import_material,
    review_import_action,
    review_import_material,
    review_import_presentation,
    understand_import,
)
from features.visuals.material_references import material_has_external_references
from features.visuals.repository import VisualStateError
from features.visuals.schemas import (
    ComboMaterialRef,
    ImportAnalysisRequest,
    ImportAnalysisResult,
    ImportAnalysisView,
    ImportComponent,
    ImportCreatedMaterial,
    ImportMatch,
    ImportObservation,
    SceneMediaRef,
)
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import compute_file_sha256, resolve_asset_reference, save_companion_asset_async
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.async_io import run_blocking
from infrastructure.database import ENGINE, SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from infrastructure.video_processing.image import prepare_transparent_image
from infrastructure.video_processing.matting import require_matting_model
from PIL import Image
from runtime.lifecycle import RuntimeParticipant
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .visuals_companion import freeze_action_reference, load_action_review_source

logger = get_logger(__name__)
_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}
_LOCKS: dict[int, asyncio.Lock] = {}
_IMPORT_CANDIDATE_LIMIT = 24


@dataclass(frozen=True, slots=True)
class _ReleasePlan:
    material: ImportCreatedMaterial
    preserved: bool
    adopt: Callable[[AsyncSession], Awaitable[None]] | None

    @property
    def note(self) -> str:
        return (
            "；本次导入先前创建的素材仍被其它内容使用，已保留"
            if self.preserved
            else "；本次导入先前创建的素材已自动撤销"
        )


def schedule_import_analysis(user_id: int, job_id: int) -> None:
    key = user_id, job_id
    if key in _TASKS and not _TASKS[key].done():
        return
    task = asyncio.create_task(run_import_analysis(user_id, job_id), name=f"visual-import-{job_id}")
    _TASKS[key] = task
    TASK_SUPERVISOR.register(task, owner="visuals.import", user_id=user_id)
    task.add_done_callback(lambda finished: _TASKS.pop(key, None) if _TASKS.get(key) is finished else None)


async def run_manual_import_job(user_id: int, job_id: int) -> bool:
    previous = _TASKS.get((user_id, job_id))
    if previous is not None and not previous.done():
        return False
    schedule_import_analysis(user_id, job_id)
    return True


async def queue_import_analysis(user_id: int, visual_id: int) -> ImportAnalysisView | None:
    view = await ensure_import_job(user_id, visual_id)
    if view is not None and view.state == "queued":
        schedule_import_analysis(user_id, view.id)
    return view


async def control_import_analysis(user_id: int, visual_id: int, request: ImportAnalysisRequest) -> ImportAnalysisView:
    if request.command == "apply_result":
        await apply_import_result(user_id, visual_id, request.expected_revision)
        return await ensure_import_job(user_id, visual_id)
    view = await command_import_job(user_id, visual_id, request)
    if request.command == "cancel":
        task = _TASKS.get((user_id, view.id))
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    elif view.state == "queued":
        previous = _TASKS.get((user_id, view.id))
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        schedule_import_analysis(user_id, view.id)
    return view


async def _hash(reference: str) -> str:
    resolved = resolve_asset_reference(reference)
    if resolved is None:
        raise ValueError("素材原件缺失")
    return await run_blocking(compute_file_sha256, resolved[0])


async def _load_context(work: ImportWork) -> None:
    context = work.context
    if context.identity is not None:
        return
    async with SESSION_LOCAL() as db:
        context.language = resolve_language(await get_user_setting(db, work.user_id, "language"))
        identity = await load_visual_identity(db, work.user_id)
        avatar = (
            await avatar_snapshot(db, work.user_id, identity.avatar_id) if identity and identity.avatar_id else None
        )
        await db.commit()
    if avatar and resolve_asset_reference(avatar.seed_fullbody_url) and resolve_asset_reference(avatar.asset_url):
        context.identity = identity
        context.identity_path, context.portrait_path = avatar.seed_fullbody_url, avatar.asset_url
        context.identity_hash, context.portrait_hash = (
            await _hash(avatar.seed_fullbody_url),
            await _hash(avatar.asset_url),
        )
    for kind in ("outfit", "scene", "action"):
        await _refresh_material_candidates(work, kind)


async def _prepare_frames(work: ImportWork) -> None:
    if await _hash(work.media_path) != work.snapshot.source.original_content_hash:
        raise ValueError("上传原件已改变，分析结果未采纳")
    if work.media_kind == "video":
        resolved = resolve_asset_reference(work.media_path)
        if resolved:
            work.context.transparent_video = (await run_blocking(probe_video, resolved[0])).has_alpha
    if work.context.frames and all(resolve_asset_reference(frame.path) for frame in work.context.frames):
        work.duration_ms = work.context.sample_duration_ms or work.duration_ms
        return
    samples = await run_blocking(sample_import, work.media_path, work.media_kind, work.duration_ms)
    work.context.animated_image = samples.animated_image
    work.context.timing_reliable = samples.timing_reliable
    work.context.sample_duration_ms = samples.duration_ms
    if samples.duration_ms is not None:
        work.duration_ms = samples.duration_ms
    work.context.frames = []
    for frame in samples.frames:
        path = await save_companion_asset_async(
            frame.data, user_id=work.user_id, label="analysis_frame", ext="png", directory=f"visuals/analysis/{work.id}"
        )
        work.context.frames.append(ImportFrameRef(seconds=frame.seconds, path=path))


async def _rank_candidates(work: ImportWork, observation: ImportObservation) -> list[ImportCatalogCandidate]:
    candidates = [entry for entry in work.context.candidates if entry.kind == observation.kind]
    target = await run_blocking(visual_vector, work.context.frames[observation.frame_index].path)
    if observation.crop:
        frame = resolve_asset_reference(work.context.frames[observation.frame_index].path)
        if frame:
            data = await run_blocking(frame[0].read_bytes)
            region = await run_blocking(extract_region, data, observation.crop, min_edge=1)
            target = await run_blocking(visual_vector_bytes, region)
    query = f"{observation.name} {observation.description} {' '.join(observation.features)}"

    def ranked() -> list[ImportCatalogCandidate]:
        ordered = sorted(
            candidates,
            key=lambda entry: retrieval_score(f"{entry.name} {entry.description}", query, entry.path, target),
            reverse=True,
        )
        # 内容完全一致的环境/着装必须进入复核，即使文本和轻量视觉召回把它排到后面。
        exact = [
            entry
            for entry in ordered
            if (entry.outfit and entry.outfit.content_hash == work.snapshot.source.original_content_hash)
            or (entry.scene and entry.scene.content_hash == work.snapshot.source.original_content_hash)
        ]
        selected = exact + [entry for entry in ordered if entry not in exact]
        return selected[:_IMPORT_CANDIDATE_LIMIT]

    return await run_blocking(ranked)


async def _refresh_material_candidates(work: ImportWork, kind: str) -> None:
    """创建前读取完整的当前可用目录，包括本任务与其他任务刚入库的素材。"""
    candidates: list[ImportCatalogCandidate] = []
    async with SESSION_LOCAL() as db:
        cursor = None
        while True:
            if kind == "outfit":
                outfits = await ready_outfit_snapshots(db, work.user_id, limit=100, cursor=cursor)
                for row in outfits:
                    if resolve_asset_reference(row.fullbody_url):
                        candidates.append(
                            ImportCatalogCandidate(
                                kind="outfit",
                                material_id=row.id,
                                name=row.name,
                                description=row.description or "",
                                path=row.fullbody_url,
                                outfit=ComboMaterialRef(
                                    material_id=row.id,
                                    name=row.name,
                                    description=row.description or "",
                                    path=row.fullbody_url,
                                    content_hash=await _hash(row.fullbody_url),
                                    revision=row.updated_at,
                                ),
                            )
                        )
                if len(outfits) < 100:
                    break
                cursor = outfits[-1].id
            elif kind == "scene":
                scenes = await list_scene_snapshots(db, work.user_id, limit=100, cursor=cursor, ready_only=True)
                for scene in scenes.scenes:
                    if resolve_asset_reference(scene.media_path):
                        candidates.append(
                            ImportCatalogCandidate(
                                kind="scene",
                                material_id=scene.id,
                                name=scene.title,
                                description=scene.description,
                                path=scene.media_path,
                                scene=SceneMediaRef(
                                    scene_id=scene.id,
                                    title=scene.title,
                                    description=scene.description,
                                    path=scene.media_path,
                                    content_hash=await _hash(scene.media_path),
                                    revision=scene.updated_at,
                                ),
                            )
                        )
                if not scenes.has_more:
                    break
                cursor = int(scenes.next_cursor)
            else:
                actions = await list_actions(db, work.user_id, limit=100, cursor=cursor)
                candidates.extend(
                    ImportCatalogCandidate(
                        kind="action",
                        material_id=action.id,
                        name=action.name,
                        description=action.motion_description,
                        action=freeze_action_reference(action),
                    )
                    for action in actions.actions
                )
                if not actions.has_more:
                    break
                cursor = int(actions.next_cursor)
        await db.commit()
    work.context.candidates = [entry for entry in work.context.candidates if entry.kind != kind] + candidates


@asynccontextmanager
async def _material_adoption_lock(work: ImportWork, kind: str) -> AsyncIterator[None]:
    """跨进程串行化同一账户、同一素材类别的复核与采纳。"""

    lock_name = f"spiritagent:visual-import:{kind}:{work.user_id}"
    async with ENGINE.connect() as raw_connection:
        connection = await raw_connection.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await connection.execute(select(func.pg_advisory_lock(func.hashtextextended(lock_name, 0))))
        except BaseException:
            await connection.invalidate()
            raise
        try:
            yield
        finally:
            try:
                await connection.execute(select(func.pg_advisory_unlock(func.hashtextextended(lock_name, 0))))
            except BaseException:
                await connection.invalidate()
                raise


async def _read_selected(work: ImportWork, kind: str, material_id: int) -> ImportCatalogCandidate:
    if work.context.presentation_review and work.context.presentation_review.human_approval:
        frozen = next(
            (entry for entry in work.context.candidates if entry.kind == kind and entry.material_id == material_id),
            None,
        )
        if frozen is not None:
            return frozen
    async with SESSION_LOCAL() as db:
        if kind == "outfit":
            row = await ready_outfit_snapshot(db, work.user_id, material_id)
            if row is None:
                raise ValueError("所选着装不存在或尚未就绪")
            await db.commit()
            ref = ComboMaterialRef(
                material_id=row.id,
                name=row.name,
                path=row.fullbody_url,
                content_hash=await _hash(row.fullbody_url),
                description=row.description or "",
                revision=row.updated_at,
            )
            return ImportCatalogCandidate(
                kind="outfit",
                material_id=row.id,
                name=row.name,
                description=row.description or "",
                path=row.fullbody_url,
                outfit=ref,
            )
        if kind == "scene":
            row = await scene_snapshot(db, work.user_id, material_id)
            if row is None or row.status != "ready":
                raise ValueError("所选环境不存在或尚未就绪")
            await db.commit()
            ref = SceneMediaRef(
                scene_id=row.id,
                title=row.title,
                path=row.media_path,
                content_hash=await _hash(row.media_path),
                description=row.description,
                revision=row.updated_at,
            )
            return ImportCatalogCandidate(
                kind="scene",
                material_id=row.id,
                name=row.title,
                description=row.description,
                path=row.media_path,
                scene=ref,
            )
        action = await action_detail(db, work.user_id, material_id)
        if action is None or action.status != "approved":
            raise ValueError("所选动作尚未通过评审，请在动作库中处理")
        await db.commit()
        return ImportCatalogCandidate(
            kind="action",
            material_id=action.id,
            name=action.name,
            description=action.motion_description,
            action=freeze_action_reference(action),
        )


def _append_created_material(component: ImportComponent, created: ImportCreatedMaterial) -> None:
    if any(
        entry.kind == created.kind
        and entry.material_id == created.material_id
        and entry.extraction_key == created.extraction_key
        for entry in component.created_materials
    ):
        return
    component.created_materials.append(created)


def _remember_created_material(component: ImportComponent) -> None:
    if not component.created or component.material_id is None:
        return
    _append_created_material(
        component,
        ImportCreatedMaterial(
            kind=component.observation.kind, material_id=component.material_id, extraction_key=component.extraction_key
        ),
    )


def _merge_created_materials(work: ImportWork, component: ImportComponent) -> None:
    """把当前导入任务前一轮分析保留的创建归属带回当前条目。"""

    current_keys = {(entry.kind, entry.material_id, entry.extraction_key) for entry in component.created_materials}
    history: dict[tuple[str, int, str], ImportCreatedMaterial] = {}
    for old in work.context.history:
        for previous in old.components:
            if (
                previous.observation.key != component.observation.key
                or previous.observation.kind != component.observation.kind
            ):
                continue
            for created in previous.created_materials:
                history[created.kind, created.material_id, created.extraction_key] = created
    for key, created in history.items():
        if key not in current_keys:
            _append_created_material(component, created.model_copy(deep=True))


def _owned_material(component: ImportComponent, material_id: int) -> ImportCreatedMaterial | None:
    return next(
        (
            entry
            for entry in component.created_materials
            if entry.kind == component.observation.kind
            and entry.material_id == material_id
            and entry.state != "released"
        ),
        None,
    )


def _current_owned_material(work: ImportWork, component: ImportComponent) -> ImportCreatedMaterial | None:
    _merge_created_materials(work, component)
    if not component.created or component.material_id is None or component.status in {"failed", "skipped", "undone"}:
        return None
    return _owned_material(component, component.material_id)


def _mark_reference(work: ImportWork, component: ImportComponent, material_id: int) -> None:
    _merge_created_materials(work, component)
    owned = _owned_material(component, material_id)
    component.material_id = material_id
    component.created = owned is not None
    component.extraction_key = owned.extraction_key if owned is not None else ""


async def _material_is_referenced(work: ImportWork, component: ImportComponent, material_id: int) -> bool:
    """检查当前任务之外的正式引用，并补上同一任务其它条目的共享引用。"""

    if work.result is not None and any(
        other is not component
        and other.observation.kind == component.observation.kind
        and other.material_id == material_id
        and other.status in {"created", "reused", "reviewing"}
        for other in work.result.components
    ):
        return True
    async with SESSION_LOCAL() as db:
        visual = await material_has_external_references(
            db,
            work.user_id,
            component.observation.kind,
            material_id,
            exclude_import_job_id=work.id,
            exclude_visual_id=work.visual_id,
        )
        life = await material_has_references(db, work.user_id, component.observation.kind, material_id)
        await db.commit()
    return visual or life


async def _release_plan(work: ImportWork, component: ImportComponent, material: ImportCreatedMaterial) -> _ReleasePlan:
    preserved = await _material_is_referenced(work, component, material.material_id)
    if preserved:
        return _ReleasePlan(material=material, preserved=True, adopt=None)
    handler = {"outfit": undo_imported_outfit, "scene": undo_imported_scene, "action": undo_imported_action}[
        component.observation.kind
    ]

    async def adopt(db: AsyncSession) -> None:
        await handler(db, work.user_id, material.material_id, material.extraction_key)

    return _ReleasePlan(material=material, preserved=False, adopt=adopt)


def _apply_release_plan(work: ImportWork, plan: _ReleasePlan) -> None:
    plan.material.state = "retained" if plan.preserved else "released"
    if not plan.preserved:
        work.context.candidates = [
            entry
            for entry in work.context.candidates
            if (entry.kind, entry.material_id) != (plan.material.kind, plan.material.material_id)
        ]


async def _change_reference(
    work: ImportWork, component: ImportComponent, material_id: int, *, previous: ImportCreatedMaterial | None = None
) -> tuple[Callable[[AsyncSession], Awaitable[None]] | None, str]:
    """切换当前关联，并准备在同一保存事务中清理不再使用的旧素材。"""

    _merge_created_materials(work, component)
    old = previous or _current_owned_material(work, component)
    if old is not None and old.material_id != material_id:
        plan = await _release_plan(work, component, old)
        if plan.preserved:
            _apply_release_plan(work, plan)
            _mark_reference(work, component, material_id)
            return None, plan.note

        async def adopt(db: AsyncSession) -> None:
            assert plan.adopt is not None
            await plan.adopt(db)
            _apply_release_plan(work, plan)
            _mark_reference(work, component, material_id)

        return adopt, plan.note
    _mark_reference(work, component, material_id)
    return None, ""


async def _restore_previous_reference(
    work: ImportWork, component: ImportComponent, previous: ImportCreatedMaterial
) -> None:
    if component.material_id == previous.material_id:
        return
    current = _owned_material(component, component.material_id) if component.created and component.material_id else None
    plan = await _release_plan(work, component, current) if current is not None else None

    def restore() -> None:
        if plan is not None:
            _apply_release_plan(work, plan)
        component.material_id = previous.material_id
        component.created = True
        component.extraction_key = previous.extraction_key
        component.status = "created"
        component.reason += "；新素材评审未完成，已保留原关联"

    if plan is None or plan.preserved:
        restore()
        await save_import_progress(work, "organize")
        return

    async def adopt(db: AsyncSession) -> None:
        assert plan.adopt is not None
        await plan.adopt(db)
        restore()

    await save_import_progress(work, "organize", adopt=adopt)


async def _cleanup_inactive_created_materials(work: ImportWork, component: ImportComponent) -> None:
    """收敛本条目不再作为当前关联的创建素材，覆盖中断后的重试路径。"""

    if component.status not in {"created", "reused", "skipped", "undone"}:
        return
    _merge_created_materials(work, component)
    current_id = (
        component.material_id
        if component.created
        and component.material_id is not None
        and component.status not in {"failed", "skipped", "undone"}
        else None
    )
    stale: list[ImportCreatedMaterial] = []
    seen: set[tuple[str, int]] = set()
    for entry in component.created_materials:
        key = entry.kind, entry.material_id
        if entry.state == "active" and entry.material_id != current_id and key not in seen:
            stale.append(entry)
            seen.add(key)
    if not stale:
        return
    plans = [await _release_plan(work, component, entry) for entry in stale]
    notes = "；".join(plan.note.removeprefix("；") for plan in plans)
    pending = [plan for plan in plans if plan.adopt is not None]
    if not pending:
        for plan in plans:
            _apply_release_plan(work, plan)
        component.reason = f"{component.reason}；{notes}" if component.reason else notes
        await save_import_progress(work, "organize")
        return

    async def adopt(db: AsyncSession) -> None:
        for plan in pending:
            assert plan.adopt is not None
            await plan.adopt(db)
        for plan in plans:
            _apply_release_plan(work, plan)
        component.reason = f"{component.reason}；{notes}" if component.reason else notes

    await save_import_progress(work, "organize", adopt=adopt)


async def _adopt_existing(work: ImportWork, component: ImportComponent, material_id: int) -> None:
    async with _material_adoption_lock(work, component.observation.kind):
        await _adopt_existing_locked(work, component, material_id)


async def _adopt_existing_locked(work: ImportWork, component: ImportComponent, material_id: int) -> None:
    selected = await _read_selected(work, component.observation.kind, material_id)
    result = await match_import_material(work, component.observation, [selected])
    component.matches = [entry for entry in component.matches if entry.material_id != material_id] + result.matches
    component.status = "reused"
    component.reason = result.matches[0].evidence if result.matches else "用户选择已有素材"
    adopt, note = await _change_reference(work, component, material_id)
    component.reason += note
    work.context.candidates = [
        entry
        for entry in work.context.candidates
        if (entry.kind, entry.material_id) != (selected.kind, selected.material_id)
    ] + [selected]
    await save_import_progress(work, "organize", adopt=adopt)


async def _remember_candidate(work: ImportWork, component: ImportComponent) -> None:
    if component.material_id is None:
        return
    selected = await _read_selected(work, component.observation.kind, component.material_id)
    work.context.candidates = [
        entry
        for entry in work.context.candidates
        if (entry.kind, entry.material_id) != (selected.kind, selected.material_id)
    ] + [selected]
    await save_import_progress(work, "organize")


async def _recheck_existing_material(work: ImportWork, component: ImportComponent, *, explicit: bool) -> bool:
    """在真正提取前复核本任务中新出现的候选，避免过时的 new 结论造成重复入库。"""

    await _refresh_material_candidates(work, component.observation.kind)
    candidates = await _rank_candidates(work, component.observation)
    if not candidates:
        return False
    result = await match_import_material(work, component.observation, candidates)
    component.matches = result.matches
    await save_import_progress(work, "match")
    if result.decision == "reuse" and result.selected_id is not None:
        await _read_selected(work, component.observation.kind, result.selected_id)
        component.status = "reused"
        component.reason = next(entry.evidence for entry in result.matches if entry.material_id == result.selected_id)
        adopt, note = await _change_reference(work, component, result.selected_id)
        component.reason += note
        await save_import_progress(work, "organize", adopt=adopt)
        return True
    if result.decision == "uncertain" and not explicit:
        component.status, component.reason = "ambiguous", "存在近似素材，请选择已有素材或保留识别结果"
        return True
    return False


async def _generate_import_material(work: ImportWork, component: ImportComponent) -> str:
    observation = component.observation
    frame = work.context.frames[observation.frame_index].path
    state = work.context.image_chains.setdefault(observation.key, ImageChainState())
    directory = state.storage_directory or f"visuals/analysis/{work.id}"
    description = json.dumps(
        {
            "name": observation.name,
            "description": observation.description,
            "features": observation.features,
            "user_corrections": component.notes,
        },
        ensure_ascii=False,
    )

    async def save_progress(progress: ImageChainState) -> None:
        work.context.image_chains[observation.key] = progress
        await save_import_progress(work, "generate_material")

    async def before_submit() -> None:
        require_new_generation_call(work.user_id)
        await guard_import_work(work, paid=True)

    if observation.kind == "scene":
        resolved = resolve_asset_reference(frame)
        if resolved is None:
            raise ValueError("原始环境参考图缺失")

        def size() -> tuple[int, int]:
            with Image.open(resolved[0]) as image:
                return image.size

        width, height = await run_blocking(size)
        paths = await generate_scene_images(
            IMPORT_SCENE_GENERATION.format(observation=description),
            user_id=work.user_id,
            storage_directory=directory,
            target_width=width,
            target_height=height,
            reference_image=frame,
            state=state,
            save_progress=save_progress,
            before_submit=before_submit,
        )
    else:
        if work.context.identity is None or not work.context.identity_path:
            raise ValueError("伙伴身份尚未就绪，无法生成着装")
        await run_blocking(require_matting_model)
        paths = await generate_reviewed_images(
            IMPORT_OUTFIT_GENERATION.format(observation=description),
            user_id=work.user_id,
            task=AiTaskKey.outfit,
            storage_directory=directory,
            size=FULLBODY_SIZE,
            reference_image=work.context.identity_path,
            secondary_reference_image=frame,
            identity_reference=work.context.identity_path,
            identity_reference_mode="primary",
            goal_review="required",
            prefer_transparent_background=True,
            state=state,
            save_progress=save_progress,
            before_submit=before_submit,
        )
    return paths[0]


async def _extract(work: ImportWork, component: ImportComponent, *, explicit: bool = False) -> None:
    async with _material_adoption_lock(work, component.observation.kind):
        await _extract_locked(work, component, explicit=explicit)


async def _extract_locked(work: ImportWork, component: ImportComponent, *, explicit: bool) -> None:
    observation = component.observation
    if work.result is None:
        raise ValueError("内容分析结果缺失")
    if observation.kind == "outfit" and (work.context.identity is None or not work.context.identity_path):
        component.status, component.failure_code, component.reason = (
            "failed",
            "identity_unavailable",
            "伙伴身份尚未就绪，无法创建着装",
        )
        return
    prepared = component.candidate_verdict == "pass" and (
        component.preview_path is not None or component.creation_method in {"pose", "motion"}
    )
    chain = work.context.image_chains.get(observation.key)
    regenerating = (
        explicit
        and component.creation_method == "generated"
        and chain is not None
        and not chain.candidates
        and chain.phase == "ready"
    )
    if not prepared and not regenerating and await _recheck_existing_material(work, component, explicit=explicit):
        return
    previous = _current_owned_material(work, component)
    component.extraction_key = component.extraction_key or str(
        uuid5(
            NAMESPACE_URL,
            f"spirit-import:{work.snapshot.source.original_content_hash}:{observation.kind}:{observation.key}",
        )
    )
    if observation.kind == "action":
        motion = bool(
            (work.media_kind == "video" or work.context.animated_image)
            and work.context.timing_reliable
            and observation.extractable
            and observation.action_design is not None
            and observation.start_seconds is not None
            and observation.end_seconds is not None
            and len(
                {
                    frame.seconds
                    for frame in work.context.frames
                    if observation.start_seconds <= frame.seconds <= observation.end_seconds
                }
            )
            >= 2
        )
        if not motion and component.creation_method != "pose":
            observation.action_design = await design_import_pose(work, observation)
            component.creation_method, component.candidate_verdict = "pose", None
            await save_import_progress(work, "action_review")
        elif motion:
            component.creation_method = "motion"
        if observation.action_design is None:
            raise ValueError("动作定义缺失")
        if component.candidate_verdict != "pass":
            evidence = await review_import_action(work, observation, pose=component.creation_method == "pose")
            component.reason, component.candidate_verdict = evidence.reason, evidence.decision
            await save_import_progress(work, "action_review")
            if evidence.decision != "pass":
                component.status = "ambiguous" if evidence.decision == "review" else "failed"
                component.failure_code = "quality_rejected" if evidence.decision == "fail" else None
                component.failure_summary = evidence.reason if evidence.decision == "fail" else ""
                return
        evidence_reason = component.reason
        async with SESSION_LOCAL() as db:
            source = await load_action_review_source(db, work.user_id)
            await db.commit()

        async def adopt(db: AsyncSession) -> None:
            current = await create_action(
                db,
                work.user_id,
                observation.action_design,
                source="user_requested",
                review_source_json=source.model_dump(mode="json"),
                import_key=component.extraction_key,
            )
            component.material_id, component.status = current.id, "reviewing"
            component.created = await action_is_imported(db, work.user_id, current.id, component.extraction_key)
            if component.created and current.status not in {"pending", "reviewing"}:
                current = await update_action(
                    db,
                    work.user_id,
                    current.id,
                    observation.action_design,
                    expected_updated_at=current.updated_at,
                    review_source_json=source.model_dump(mode="json"),
                )
            _remember_created_material(component)

        await save_import_progress(work, "action_review", adopt=adopt)
        async with SESSION_LOCAL() as db:
            current = await action_detail(db, work.user_id, component.material_id)
            if current and (current.status == "failed" or (current.status == "deferred" and component.notes)):
                current = await retry_review(
                    db,
                    work.user_id,
                    current.id,
                    expected_updated_at=current.updated_at,
                    review_source_json=source.model_dump(mode="json"),
                )
                await db.commit()
        if current and current.status == "pending":
            await guard_import_work(work)
            require_new_generation_call(work.user_id)
            await review_action(work.user_id, current.id, current.updated_at)
        async with SESSION_LOCAL() as db:
            current = await action_detail(db, work.user_id, component.material_id)
        component.status = (
            ("created" if component.created else "reused")
            if current and current.status == "approved"
            else "failed"
            if current and current.status == "failed"
            else "recognized"
        )
        component.reason = f"{evidence_reason}\n{current.review_reason}" if current else "动作已被删除"
        if current and current.status in {"deferred", "rejected"}:
            component.reason += "\n动作尚未入库，可在动作库中调整后重新评审。"
        if component.status in {"created", "reused"}:
            if previous is not None and previous.material_id != component.material_id:
                adopt, note = await _change_reference(work, component, component.material_id, previous=previous)
                component.reason += note
                await save_import_progress(work, "organize", adopt=adopt)
            await _remember_candidate(work, component)
        elif previous is not None and component.material_id != previous.material_id:
            await _restore_previous_reference(work, component, previous)
        if component.status not in {"created", "reused"}:
            if current and current.status == "deferred":
                component.status, component.failure_code = "ambiguous", None
            else:
                component.status, component.failure_code, component.failure_summary = (
                    "failed",
                    "action_rejected",
                    current.review_reason if current else "动作已被删除",
                )
        return
    frame = work.context.frames[observation.frame_index]
    direct = bool(
        observation.extractable
        and component.creation_method != "generated"
        and component.candidate_verdict not in {"review", "fail"}
        and (observation.kind != "outfit" or work.result.understanding.identity_match == "same")
    )
    if direct:
        resolved = resolve_asset_reference(component.preview_path) if component.preview_path else None
        if resolved is not None:
            data = await run_blocking(resolved[0].read_bytes)
        else:
            resolved = resolve_asset_reference(frame.path)
            if resolved is None:
                raise ValueError("提取所需的原始帧缺失")
            data = await run_blocking(resolved[0].read_bytes)
            data = await run_blocking(extract_region, data, observation.crop)
            try:
                if observation.kind == "outfit":
                    data = await run_blocking(prepare_transparent_image, data)
            except VideoProcessError:
                direct = False
            if direct:
                component.preview_path = await save_companion_asset_async(
                    data,
                    user_id=work.user_id,
                    label=f"extracted_{observation.kind}",
                    ext="png",
                    directory=f"visuals/analysis/{work.id}",
                )
        if direct:
            component.creation_method = "pixels"
            await save_import_progress(work, "extraction_review")
            if component.candidate_verdict != "pass":
                verdict = await review_import_material(work, observation, data, creation_method="pixels")
                component.reason, component.candidate_verdict = verdict.reason, verdict.decision
                await save_import_progress(work, "extraction_review")
                if verdict.decision != "pass":
                    direct = False

    if not direct:
        resolved = (
            resolve_asset_reference(component.preview_path)
            if component.preview_path and component.creation_method == "generated"
            else None
        )
        if resolved is None:
            if component.creation_method != "generated":
                component.preview_path = None
            component.creation_method, component.candidate_verdict = "generated", None
            await save_import_progress(work, "generate_material")
            component.preview_path = await _generate_import_material(work, component)
            await save_import_progress(work, "extraction_review")
            resolved = resolve_asset_reference(component.preview_path)
            if resolved is None:
                raise ValueError("生成的素材图片缺失")
        if component.candidate_verdict != "pass":
            data = await run_blocking(resolved[0].read_bytes)
            verdict = await review_import_material(work, observation, data, creation_method="generated")
            component.reason, component.candidate_verdict = verdict.reason, verdict.decision
            await save_import_progress(work, "extraction_review")
            if verdict.decision != "pass":
                component.status = "ambiguous" if verdict.decision == "review" else "failed"
                component.failure_code = "quality_rejected" if verdict.decision == "fail" else None
                component.failure_summary = verdict.reason if verdict.decision == "fail" else ""
                return

    generated = component.creation_method == "generated"
    description = observation.description
    if generated:
        description = f"{description}\n根据上传影像生成补全，遮挡部分为推测。{component.reason}"
    image_chain = work.context.image_chains.get(observation.key) if generated else None
    generation_origin = image_chain.selected_origin() if image_chain is not None else None

    async def adopt(db: AsyncSession) -> None:
        if observation.kind == "outfit":
            accepted = await accept_imported_outfit(
                db,
                work.user_id,
                source_key=component.extraction_key,
                path=component.preview_path,
                name=observation.name,
                description=description,
                identity=work.context.identity,
                identity_path=work.context.identity_path,
                reference_path=frame.path if generated else None,
                generation_origin=generation_origin,
            )
        else:
            accepted = await accept_imported_scene(
                db,
                work.user_id,
                source_key=component.extraction_key,
                path=component.preview_path,
                name=observation.name,
                description=description,
                generated=generated,
                reference_path=frame.path if generated else "",
                generation_origin=generation_origin,
            )
        component.material_id, component.created, component.status = accepted.id, True, "created"
        _remember_created_material(component)

    await save_import_progress(work, "organize", adopt=adopt)
    if previous is not None and previous.material_id != component.material_id:
        adopt, note = await _change_reference(work, component, component.material_id, previous=previous)
        component.reason += note
        await save_import_progress(work, "organize", adopt=adopt)
    # 当前任务新建的素材立即回到候选集，后续条目可以复用它，避免同一上传
    # 在多个片段中重复创建近似素材。
    await _remember_candidate(work, component)


async def _undo(work: ImportWork, component: ImportComponent, *, status: str = "undone") -> None:
    if component.status in {"undone", "skipped"}:
        return
    if not component.created or component.material_id is None:
        raise ValueError("只有这次导入创建的素材可以撤销入库")

    _merge_created_materials(work, component)
    owned = _owned_material(component, component.material_id)
    if owned is None:
        raise ValueError("这条素材的创建来源已丢失，请重新分析后再撤销")
    plan = await _release_plan(work, component, owned)

    def detach() -> None:
        _apply_release_plan(work, plan)
        component.material_id, component.created, component.extraction_key = None, False, ""
        component.status = status
        component.reason = (
            "本次关联已解除；素材仍被其它内容使用，已保留"
            if plan.preserved
            else "已撤销入库，原始影像仍保留"
            if status == "undone"
            else "已跳过这条素材关联"
        )

    if plan.preserved:
        detach()
        await save_import_progress(work, "organize")
        return

    async def adopt(db: AsyncSession) -> None:
        assert plan.adopt is not None
        await plan.adopt(db)
        detach()

    await save_import_progress(work, "organize", adopt=adopt)


async def _organize_component(work: ImportWork, component: ImportComponent) -> None:
    choice = next((entry for entry in work.selections if entry.key == component.observation.key), None)
    if choice:
        if (
            component.status in {"created", "reused"}
            and component.material_id is not None
            and (
                choice.command in {"extract", "retry", "regenerate"}
                or (choice.command == "existing" and choice.material_id == component.material_id)
            )
        ):
            return
        if choice.command in {"extract", "retry", "regenerate"}:
            chain = work.context.image_chains.get(component.observation.key)
            restart = choice.command == "regenerate" or extraction_command(component, chain) == "regenerate"
            if restart:
                if chain is not None and (
                    chain.pending_urls
                    or chain.pending_path
                    or chain.phase in {"submitting", "storing", "evaluating"}
                    or chain.stop_reason == "result_unknown"
                ):
                    raise ImageGenerationError(
                        "已有结果需要继续处理", result_unknown=chain.stop_reason == "result_unknown"
                    )
                work.context.image_chains.pop(component.observation.key, None)
                if component.creation_method == "pose":
                    component.observation.action_design = None
                component.preview_path, component.candidate_verdict, component.creation_method = None, None, None
            elif component.candidate_verdict in {"review", "fail"}:
                component.status, component.failure_code = "failed", "quality_rejected"
                return
        if choice.command == "undo":
            await _undo(work, component)
        elif choice.command == "skip":
            if component.created and component.material_id is not None:
                await _undo(work, component, status="skipped")
            else:
                component.material_id, component.status, component.reason = None, "skipped", "已跳过这条素材关联"
        elif choice.command == "existing":
            await _adopt_existing(work, component, choice.material_id)
        else:
            await _extract(work, component, explicit=True)
        return
    if component.status in {"reused", "created", "skipped", "undone", "ambiguous"}:
        return
    if work.media_kind == "image" and component.observation.crop is None:
        exact = [
            entry
            for entry in work.context.candidates
            if entry.kind == component.observation.kind
            and (
                (entry.outfit and entry.outfit.content_hash == work.snapshot.source.original_content_hash)
                or (entry.scene and entry.scene.content_hash == work.snapshot.source.original_content_hash)
            )
        ]
        if exact:
            component.matches = [
                ImportMatch(
                    material_id=entry.material_id,
                    name=entry.name,
                    decision="same",
                    confidence="high",
                    evidence="原件与这份已有素材的文件内容完全一致",
                )
                for entry in exact
            ]
            component.status = "reused" if len(exact) == 1 else "ambiguous"
            if len(exact) == 1:
                async with _material_adoption_lock(work, component.observation.kind):
                    adopt, note = await _change_reference(work, component, exact[0].material_id)
                    component.reason = "复用内容完全一致的已有素材" + note
                    await save_import_progress(work, "organize", adopt=adopt)
            else:
                component.material_id = None
                component.reason = "多份已有素材使用相同文件，请选择需要的关联"
            return
    await _extract(work, component)


async def run_import_analysis(user_id: int, job_id: int) -> None:
    work: ImportWork | None = None
    try:
        async with _LOCKS.setdefault(user_id, asyncio.Lock()):
            work = await claim_import_job(user_id, job_id)
            if work is None:
                return
            with asset_write_context():
                await _load_context(work)
                await _prepare_frames(work)
                await save_import_progress(work, "analyze")
                if work.result is None:
                    understanding = await understand_import(work)
                    work.result = ImportAnalysisResult(
                        understanding=understanding,
                        components=[ImportComponent(observation=entry) for entry in understanding.observations],
                    )
                    await save_import_progress(work, "organize")
                if work.result.understanding.asset_kind == "actor" and work.context.actor_asset is None:
                    await save_import_progress(work, "prepare_actor")
                    work.context.actor_asset = await prepare_imported_actor(work.media_path, work.user_id, work.id)
                    await save_import_progress(work, "organize")
                while True:
                    while (component := await take_import_operation(work)) is not None:
                        await guard_import_work(work)
                        try:
                            await _organize_component(work, component)
                            async with _material_adoption_lock(work, component.observation.kind):
                                await _cleanup_inactive_created_materials(work, component)
                        except (GenerationWorkPaused, asyncio.CancelledError):
                            raise
                        except Exception as exc:
                            logger.exception(
                                "import material organization failed",
                                extra={"user_id": user_id, "job_id": job_id, "component": component.observation.key},
                            )
                            if component.material_id is None or component.status not in {"created", "reused"}:
                                component.status = (
                                    "reviewing" if isinstance(exc, ImageReviewUnavailableError) else "failed"
                                )
                            component.failure_code = (
                                "save_failed"
                                if component.operation_stage == "save_material"
                                else "result_download_failed"
                                if isinstance(exc, ImageResultDownloadError)
                                else "result_unknown"
                                if isinstance(exc, ImageGenerationError) and exc.result_unknown
                                else "processing_failed"
                                if isinstance(exc, ImageReviewUnavailableError)
                                else "generation_failed"
                                if isinstance(exc, ImageGenerationError)
                                else "processing_failed"
                            )
                            component.reason = str(exc)
                            component.failure_summary = (
                                str(exc)
                                if isinstance(exc, (ImageGenerationError, VisualStateError))
                                else "处理未完成，已保留原件与进度，请重试。"
                            )
                        await save_import_progress(work, component.operation_stage or "organize", finish=True)
                    selected = []
                    for component in work.result.components:
                        if component.material_id is not None and component.status in {"created", "reused"}:
                            selected.append(
                                await _read_selected(work, component.observation.kind, component.material_id)
                            )
                    await save_import_progress(work, "presentation_review")
                    verdict = (
                        ImportQualityVerdict(decision="review", reason="素材整理尚未完成，原件与已完成结果已保存")
                        if any(
                            entry.status not in {"created", "reused", "skipped", "undone"}
                            for entry in work.result.components
                        )
                        else await review_import_presentation(work, selected)
                    )
                    if await complete_import_analysis(work, selected, verdict.decision, verdict.reason):
                        break

    except GenerationWorkPaused:
        if work:
            await fail_import_work(work, "账户维护期间暂停分析，原件和已完成结果保留", paused=True)
    except asyncio.CancelledError:
        if work:
            await fail_import_work(work, "分析已中断，原件和已完成结果保留", paused=True)
        raise
    except Exception as exc:
        logger.exception("visual import analysis failed", extra={"user_id": user_id, "job_id": job_id})
        if work:
            await fail_import_work(
                work,
                str(exc)
                if isinstance(exc, (ImageGenerationError, VideoProcessError, VisualStateError))
                else "分析未完成，请检查影像分析模型、素材和服务后重试",
            )
    finally:
        directories = {f"visuals/analysis/{job_id}"}
        if work is not None:
            directories.update(
                chain.storage_directory
                for chain in work.context.image_chains.values()
                if chain.storage_directory and chain.storage_directory.startswith("visuals/analysis/")
            )
        cleanup_asset_directories_after_task(user_id, sorted(directories))


async def resume_import_analysis(user_id: int | None = None) -> None:
    for owner, job_id in await recover_import_jobs(user_id):
        schedule_import_analysis(owner, job_id)


async def drain_import_analysis() -> None:
    await TASK_SUPERVISOR.drain(owner="visuals.import")
    await recover_import_jobs()


def import_runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "visuals.import",
            recover=resume_import_analysis,
            resume_user=resume_import_analysis,
            drain=drain_import_analysis,
        ),
    )
