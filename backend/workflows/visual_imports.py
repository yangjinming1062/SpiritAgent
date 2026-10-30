"""用户影像分析、分类型查重、受控提取和呈现复核的跨业务编排。"""

import asyncio
from uuid import NAMESPACE_URL, uuid5

from features.action_library.import_materials import action_is_imported, undo_imported_action
from features.action_library.queries import action_detail, list_actions
from features.action_library.review import review_action
from features.action_library.service import create_action, retry_review
from features.appearance.character_card import load_character_snapshot
from features.appearance.import_materials import accept_imported_outfit, undo_imported_outfit
from features.appearance.queries import avatar_snapshot, ready_outfit_snapshot, ready_outfit_snapshots
from features.assets.lifecycle import cleanup_asset_directories_after_task
from features.configuration.settings.values import get_user_setting
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
)
from features.visuals.import_media import (
    extract_region,
    prepare_imported_actor,
    retrieval_score,
    sample_import,
    visual_vector,
    visual_vector_bytes,
)
from features.visuals.import_publication import complete_import_analysis
from features.visuals.import_reasoning import (
    match_import_material,
    review_import_action,
    review_import_material,
    review_import_presentation,
    understand_import,
)
from features.visuals.schemas import (
    ComboMaterialRef,
    ImportAnalysisRequest,
    ImportAnalysisResult,
    ImportAnalysisView,
    ImportComponent,
    ImportMatch,
    ImportObservation,
    SceneMediaRef,
)
from infrastructure.assets.asset_store import (
    compute_file_sha256,
    resolve_asset_reference,
    save_companion_asset_async,
)
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.async_io import run_blocking
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from infrastructure.video_processing.ffmpeg import VideoProcessError, probe_video
from infrastructure.video_processing.image import prepare_transparent_image
from runtime.lifecycle import RuntimeParticipant
from runtime.paid_work import GenerationWorkPaused, require_new_generation_call
from runtime.task_supervisor import TASK_SUPERVISOR
from sqlalchemy.ext.asyncio import AsyncSession

from .visuals_companion import freeze_action_reference, load_action_review_source

logger = get_logger(__name__)
_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}
_LOCKS: dict[int, asyncio.Lock] = {}
_IMPORT_CANDIDATE_LIMIT = 24


def schedule_import_analysis(user_id: int, job_id: int) -> None:
    key = user_id, job_id
    if key in _TASKS and not _TASKS[key].done():
        return
    task = asyncio.create_task(run_import_analysis(user_id, job_id), name=f"visual-import-{job_id}")
    _TASKS[key] = task
    TASK_SUPERVISOR.register(task, owner="visuals.import", user_id=user_id)
    task.add_done_callback(lambda finished: _TASKS.pop(key, None) if _TASKS.get(key) is finished else None)


async def queue_import_analysis(user_id: int, visual_id: int) -> ImportAnalysisView | None:
    view = await ensure_import_job(user_id, visual_id)
    if view is not None and view.state == "queued":
        schedule_import_analysis(user_id, view.id)
    return view


async def control_import_analysis(user_id: int, visual_id: int, request: ImportAnalysisRequest) -> ImportAnalysisView:
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
    if context.candidates or context.identity is not None:
        return
    async with SESSION_LOCAL() as db:
        context.language = resolve_language(await get_user_setting(db, work.user_id, "language"))
        identity = await load_character_snapshot(db, work.user_id)
        avatar = (
            await avatar_snapshot(db, work.user_id, identity.avatar_id) if identity and identity.avatar_id else None
        )
        outfits = []
        cursor = None
        while True:
            page = await ready_outfit_snapshots(db, work.user_id, limit=100, cursor=cursor)
            outfits.extend(page)
            if len(page) < 100:
                break
            cursor = page[-1].id
        scenes = []
        cursor = None
        while True:
            page = await list_scene_snapshots(db, work.user_id, limit=100, cursor=cursor, ready_only=True)
            scenes.extend(page.scenes)
            if not page.has_more:
                break
            cursor = int(page.next_cursor)
        actions = []
        cursor = None
        while True:
            page = await list_actions(db, work.user_id, limit=100, cursor=cursor)
            actions.extend(page.actions)
            if not page.has_more:
                break
            cursor = int(page.next_cursor)
        await db.commit()
    if avatar and resolve_asset_reference(avatar.seed_fullbody_url) and resolve_asset_reference(avatar.asset_url):
        context.identity = identity
        context.identity_path, context.portrait_path = avatar.seed_fullbody_url, avatar.asset_url
        context.identity_hash, context.portrait_hash = (
            await _hash(avatar.seed_fullbody_url),
            await _hash(avatar.asset_url),
        )
    for outfit in outfits:
        if not resolve_asset_reference(outfit.fullbody_url):
            continue
        ref = ComboMaterialRef(
            material_id=outfit.id,
            name=outfit.name,
            description=outfit.description or "",
            path=outfit.fullbody_url,
            content_hash=await _hash(outfit.fullbody_url),
            revision=outfit.updated_at,
        )
        context.candidates.append(
            ImportCatalogCandidate(
                kind="outfit",
                material_id=outfit.id,
                name=outfit.name,
                description=outfit.description or "",
                path=outfit.fullbody_url,
                outfit=ref,
            )
        )
    for scene in scenes:
        if not resolve_asset_reference(scene.media_path):
            continue
        ref = SceneMediaRef(
            scene_id=scene.id,
            title=scene.title,
            description=scene.description,
            path=scene.media_path,
            content_hash=await _hash(scene.media_path),
            revision=scene.updated_at,
        )
        context.candidates.append(
            ImportCatalogCandidate(
                kind="scene",
                material_id=scene.id,
                name=scene.title,
                description=scene.description,
                path=scene.media_path,
                scene=ref,
            )
        )
    for action in actions:
        context.candidates.append(
            ImportCatalogCandidate(
                kind="action",
                material_id=action.id,
                name=action.name,
                description=action.motion_description,
                action=freeze_action_reference(action),
            )
        )


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


async def _read_selected(work: ImportWork, kind: str, material_id: int) -> ImportCatalogCandidate:
    async with SESSION_LOCAL() as db:
        if kind == "outfit":
            row = await ready_outfit_snapshot(db, work.user_id, material_id)
            if row is None:
                raise ValueError("所选着装不存在或尚未就绪")
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
        return ImportCatalogCandidate(
            kind="action",
            material_id=action.id,
            name=action.name,
            description=action.motion_description,
            action=freeze_action_reference(action),
        )


def _mark_reference(work: ImportWork, component: ImportComponent, material_id: int) -> None:
    owned = component.created and component.material_id == material_id
    component.material_id = material_id
    component.created = owned
    if not owned:
        component.extraction_key = ""
    for old in work.context.history:
        previous = next(
            (
                entry
                for entry in old.components
                if entry.observation.kind == component.observation.kind
                and entry.material_id == material_id
                and entry.created
            ),
            None,
        )
        if previous:
            component.created, component.extraction_key = True, previous.extraction_key


async def _adopt_existing(work: ImportWork, component: ImportComponent, material_id: int) -> None:
    selected = await _read_selected(work, component.observation.kind, material_id)
    result = await match_import_material(work, component.observation, [selected])
    component.matches = [entry for entry in component.matches if entry.material_id != material_id] + result.matches
    _mark_reference(work, component, material_id)
    component.status = "reused"
    component.reason = result.matches[0].evidence if result.matches else "用户选择已有素材"
    work.context.candidates = [
        entry
        for entry in work.context.candidates
        if (entry.kind, entry.material_id) != (selected.kind, selected.material_id)
    ] + [selected]
    await save_import_progress(work, "organize")


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


async def _recheck_existing_material(work: ImportWork, component: ImportComponent) -> bool:
    """在真正提取前复核本任务中新出现的候选，避免过时的 new 结论造成重复入库。"""

    if component.observation.kind not in {"outfit", "scene"}:
        return False
    candidates = await _rank_candidates(work, component.observation)
    if not candidates:
        return False
    result = await match_import_material(work, component.observation, candidates)
    component.matches = result.matches
    await save_import_progress(work, "match")
    if result.decision == "reuse" and result.selected_id is not None:
        await _read_selected(work, component.observation.kind, result.selected_id)
        _mark_reference(work, component, result.selected_id)
        component.status = "reused"
        component.reason = next(entry.evidence for entry in result.matches if entry.material_id == result.selected_id)
        return True
    if result.decision == "uncertain":
        component.status, component.reason = "ambiguous", "存在近似素材，请选择已有素材或保留识别结果"
        return True
    return False


async def _extract(work: ImportWork, component: ImportComponent) -> None:
    observation = component.observation
    if not observation.extractable:
        component.status, component.reason = "recognized", observation.extraction_reason
        return
    if work.result is None:
        raise ValueError("内容分析结果缺失")
    if observation.kind == "outfit" and (
        work.result.understanding.identity_match != "same" or work.context.identity is None
    ):
        component.status, component.reason = "recognized", "伙伴身份尚未确认，不将这套着装加入衣柜"
        return
    if await _recheck_existing_material(work, component):
        return
    component.extraction_key = component.extraction_key or str(
        uuid5(
            NAMESPACE_URL,
            f"spirit-import:{work.snapshot.source.original_content_hash}:{observation.kind}:{observation.key}",
        )
    )
    if observation.kind == "action":
        if (
            (work.media_kind != "video" and not work.context.animated_image)
            or not work.context.timing_reliable
            or observation.action_design is None
            or len(work.context.frames) < 2
        ):
            component.status, component.reason = "recognized", "图片或不足的时间信息不能建立连续动作"
            return
        evidence = await review_import_action(work, observation)
        component.reason = evidence.reason
        if evidence.decision != "pass":
            component.status = "recognized"
            return
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

        await save_import_progress(work, "action_review", adopt=adopt)
        async with SESSION_LOCAL() as db:
            current = await action_detail(db, work.user_id, component.material_id)
            if current and current.status == "failed":
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
        component.reason = f"{evidence.reason}\n{current.review_reason}" if current else "动作已被删除"
        if component.status in {"created", "reused"}:
            await _remember_candidate(work, component)
        return
    if component.preview_path and resolve_asset_reference(component.preview_path):
        data = await run_blocking(resolve_asset_reference(component.preview_path)[0].read_bytes)
    else:
        frame = work.context.frames[observation.frame_index]
        resolved = resolve_asset_reference(frame.path)
        if resolved is None:
            raise ValueError("提取所需的原始帧缺失")
        data = await run_blocking(resolved[0].read_bytes)
        data = await run_blocking(extract_region, data, observation.crop)
        if observation.kind == "outfit":
            data = await run_blocking(prepare_transparent_image, data)
        component.preview_path = await save_companion_asset_async(
            data,
            user_id=work.user_id,
            label=f"extracted_{observation.kind}",
            ext="png",
            directory=f"visuals/analysis/{work.id}",
        )
        await save_import_progress(work, "extraction_review")
    verdict = await review_import_material(work, observation, data)
    component.reason = verdict.reason
    if verdict.decision != "pass":
        component.status = "recognized"
        return

    async def adopt(db: AsyncSession) -> None:
        if observation.kind == "outfit":
            accepted = await accept_imported_outfit(
                db,
                work.user_id,
                source_key=component.extraction_key,
                path=component.preview_path,
                name=observation.name,
                description=observation.description,
                identity=work.context.identity,
                identity_path=work.context.identity_path,
            )
        else:
            accepted = await accept_imported_scene(
                db,
                work.user_id,
                source_key=component.extraction_key,
                path=component.preview_path,
                name=observation.name,
                description=observation.description,
            )
        component.material_id, component.created, component.status = accepted.id, True, "created"

    await save_import_progress(work, "organize", adopt=adopt)
    # 当前任务新建的素材立即回到候选集，后续条目可以复用它，避免同一上传
    # 在多个片段中重复创建近似素材。
    await _remember_candidate(work, component)


async def _undo(work: ImportWork, component: ImportComponent) -> None:
    if component.status == "undone":
        return
    if not component.created or component.material_id is None:
        raise ValueError("只有这次导入创建的素材可以撤销入库")

    async def adopt(db: AsyncSession) -> None:
        handlers = {"outfit": undo_imported_outfit, "scene": undo_imported_scene, "action": undo_imported_action}
        await handlers[component.observation.kind](db, work.user_id, component.material_id, component.extraction_key)
        component.material_id, component.status, component.reason = None, "undone", "已撤销入库，原始影像仍保留"

    await save_import_progress(work, "organize", adopt=adopt)


async def _organize_component(work: ImportWork, component: ImportComponent) -> None:
    choice = next((entry for entry in work.selections if entry.key == component.observation.key), None)
    if choice:
        if choice.command == "undo":
            await _undo(work, component)
        elif choice.command == "skip":
            component.material_id, component.status, component.reason = None, "skipped", "已跳过这条素材关联"
        elif choice.command == "existing":
            await _adopt_existing(work, component, choice.material_id)
        else:
            if any(match.decision == "same" and match.confidence == "high" for match in component.matches):
                raise ValueError("已有高置信度匹配素材，请先核对关联，避免重复入库")
            await _extract(work, component)
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
                _mark_reference(work, component, exact[0].material_id)
            else:
                component.material_id = None
            component.reason = (
                "复用内容完全一致的已有素材" if len(exact) == 1 else "多份已有素材使用相同文件，请选择需要的关联"
            )
            return
    candidates = await _rank_candidates(work, component.observation)
    result = await match_import_material(work, component.observation, candidates)
    component.matches = result.matches
    await save_import_progress(work, "match")
    if result.decision == "reuse" and result.selected_id is not None:
        # 候选已经由实际图像复核；只在执行采用前重新核对素材可用性。
        await _read_selected(work, component.observation.kind, result.selected_id)
        _mark_reference(work, component, result.selected_id)
        component.status = "reused"
        component.reason = next(entry.evidence for entry in result.matches if entry.material_id == result.selected_id)
    elif result.decision == "uncertain":
        component.status, component.reason = "ambiguous", "存在近似素材，请选择已有素材或保留识别结果"
    else:
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
                for component in work.result.components:
                    await guard_import_work(work)
                    try:
                        await _organize_component(work, component)
                    except (GenerationWorkPaused, asyncio.CancelledError):
                        raise
                    except Exception as exc:
                        logger.exception(
                            "import material organization failed",
                            extra={"user_id": user_id, "job_id": job_id, "component": component.observation.key},
                        )
                        component.status, component.reason = (
                            "failed",
                            str(exc)
                            if isinstance(exc, VideoProcessError)
                            else "素材整理未完成，可保留原件并重试此条目",
                        )
                    await save_import_progress(work, "organize")
                selected = []
                for component in work.result.components:
                    if component.material_id is not None and component.status in {"created", "reused"}:
                        selected.append(await _read_selected(work, component.observation.kind, component.material_id))
                await save_import_progress(work, "presentation_review")
                verdict = await review_import_presentation(work, selected)
                await complete_import_analysis(work, selected, verdict.decision == "pass", verdict.reason)
    except GenerationWorkPaused:
        if work:
            await fail_import_work(work, "账户维护期间暂停分析，原件和已完成结果保留", paused=True)
    except asyncio.CancelledError:
        if work:
            await fail_import_work(work, "分析已中断，原件和已完成结果保留", paused=True)
        raise
    except Exception:
        logger.exception("visual import analysis failed", extra={"user_id": user_id, "job_id": job_id})
        if work:
            await fail_import_work(work, "分析未完成，请检查影像分析模型、素材和服务后重试")
    finally:
        cleanup_asset_directories_after_task(user_id, [f"visuals/analysis/{job_id}"])


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
