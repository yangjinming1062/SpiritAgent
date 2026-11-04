"""导入续审不整理、入库或应用关联；各素材槽位独立恢复。"""

from copy import deepcopy

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.assets.asset_store import client_asset_url
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from runtime.events.emit import emit_ws_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.appearance.identity import visual_identity_is_current
from features.media.chain import MediaCandidate
from features.media.character_images import ImageChainState, review_image_chain
from features.media.image_generation import ImageGenerationError
from features.media.manual_reviews import (
    ManualAction,
    approve_candidate,
    approve_image_chain,
    manual_candidate_id,
    manual_candidate_views,
    regenerate_image_chain,
)
from features.media.review_contracts import needs_review
from features.media.review_views import (
    MediaJobView,
    MediaManualRequest,
    MediaReviewRequest,
    MediaSlotView,
    review_request_matches,
)

from .import_jobs import ImportContext, ImportWork, claim_import_job, save_import_progress
from .import_operations import ImportOperation, import_operations, import_result_view
from .import_publication import update_pending_import_review
from .import_reasoning import review_import_presentation
from .models import VisualImportJob
from .repository import notify_visuals_updated, visual_item, visual_snapshot
from .schemas import ImportAnalysisResult


async def import_review_view(user_id: int, job_id: int) -> MediaJobView:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob).where(VisualImportJob.id == job_id, VisualImportJob.user_id == user_id)
        )
        if job is None:
            raise RequestError(status_code=404, message="找不到导入任务")
        context = ImportContext.model_validate(job.context_json)
        report = context.presentation_review
        item = await visual_item(db, user_id, job.visual_id)
        result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
        if result is not None:
            result = import_result_view(result, context.image_chains, import_operations(job.selections_json), job.state)
        path = context.actor_asset.media_path if context.actor_asset else item.media_path
        slots = [
            MediaSlotView(
                slot=0,
                media_id=f"{job.id}:presentation",
                label=("Prepared media" if context.actor_asset else "Uploaded media")
                if context.language == "en"
                else ("处理后的影像" if context.actor_asset else "上传影像"),
                status=job.stage,
                review=report,
                basis_id=report.evidence.basis_id if report and report.evidence else None,
                can_review=job.stage == "review_pending"
                and report is not None
                and report.evidence is not None
                and report.verdict == "review"
                and report.status != "blocked",
                candidates=await manual_candidate_views(
                    [MediaCandidate(path=path, attempt=0, review=report)], media_type=item.media_kind
                )
                if result
                else [],
            )
        ]
        for index, component in enumerate(result.components if result else (), 1):
            chain = context.image_chains.get(component.observation.key)
            candidate = (chain.best() or next(iter(chain.candidates), None)) if chain else None
            report = candidate.review if candidate else context.manual_reviews.get(component.observation.key)
            evidence = report.evidence if report else candidate.evidence if candidate else None
            slots.append(
                MediaSlotView(
                    slot=index,
                    media_id=component.observation.key,
                    label=component.observation.name,
                    status=component.operation_state,
                    review=report,
                    basis_id=evidence.basis_id if evidence else None,
                    preview_url=client_asset_url(candidate.path) if candidate else None,
                    can_review=evidence is not None
                    and (report is None or report.verdict == "review")
                    and needs_review(report),
                    candidates=await manual_candidate_views(
                        chain.candidates
                        if chain
                        else [MediaCandidate(path=component.preview_path, attempt=0, review=report)]
                        if component.preview_path
                        else []
                    ),
                    can_regenerate=component.operation_state in {"paused", "failed"}
                    and component.observation.kind != "action",
                )
            )
        return MediaJobView(
            id=job.id,
            kind=job.kind,
            state=job.state,
            stage=job.stage,
            revision=job.revision,
            slots=slots,
            required_slots=len(slots),
            ready_slots=sum(slot.review is not None and slot.review.verdict == "pass" for slot in slots),
            complete=job.state == "succeeded",
        )


async def review_import_job(user_id: int, job_id: int, slot: int, expected: MediaReviewRequest) -> MediaJobView:
    view = await import_review_view(user_id, job_id)
    target = next((entry for entry in view.slots if entry.slot == slot and entry.can_review), None)
    if target is None:
        raise RequestError(status_code=409, message="导入检查状态已变化")
    async with SESSION_LOCAL() as db:
        job = await db.get(VisualImportJob, job_id, with_for_update=True)
        if not review_request_matches(await import_review_view(user_id, job_id), expected):
            raise RequestError(status_code=409, message="检查依据或任务版本已变化")
        job.state = "queued"
        await db.commit()
    work = await claim_import_job(user_id, job_id)
    if work is None:
        raise RequestError(status_code=409, message="导入检查正在进行")
    work.context.review_only = True
    try:
        if slot == 0:
            await review_import_presentation(work, [])
        else:
            chain = work.context.image_chains[target.media_id]

            async def save(value: object) -> None:
                await save_import_progress(work, "reviewing")

            await review_image_chain(user_id, chain, task=AiTaskKey.visuals, save_progress=save)
    except ImageGenerationError:
        pass
    finally:
        async with SESSION_LOCAL() as db:
            job = await db.get(VisualImportJob, job_id, with_for_update=True)
            if job.state == "running" and job.context_json.get("execution_key") == work.context.execution_key:
                work.context.review_only = False
                job.context_json = work.context.model_dump(mode="json")
                job.state, job.stage = "paused", "confirmation"
                report = work.context.presentation_review
                if slot == 0:
                    update_pending_import_review(work)
                    job.context_json = work.context.model_dump(mode="json")
                    job.result_json = work.result.model_dump(mode="json") if work.result else job.result_json
                if (
                    slot == 0
                    and report
                    and report.verdict == "review"
                    or slot > 0
                    and work.context.image_chains[target.media_id].best() is None
                ):
                    job.stage = "review_pending"
                await notify_visuals_updated(db, user_id)
                emit_ws_event(db, user_id=user_id, event_type="media.review.updated", payload={"job_id": str(job_id)})
                await db.commit()
    return await import_review_view(user_id, job_id)


async def manually_review_import(
    db: AsyncSession, user_id: int, job_id: int, request: MediaManualRequest
) -> ManualAction:
    job = await db.get(VisualImportJob, job_id)
    original_context = deepcopy(job.context_json)
    context = ImportContext.model_validate(job.context_json)
    result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
    item = await visual_item(db, user_id, job.visual_id)
    snapshot = visual_snapshot(item)
    if result is None or request.slot > len(result.components):
        raise RequestError(status_code=409, message="原分析条目已变化，请刷新")
    if context.identity and not await visual_identity_is_current(db, user_id, context.identity):
        raise RequestError(status_code=409, message="原任务身份已失效，请另建分析或制作请求")
    if request.slot == 0:
        if request.action != "approve":
            raise RequestError(status_code=409, message="导入原件已经收藏，请通过原分析入口重新分析")
        selected = MediaCandidate(
            path=context.actor_asset.media_path if context.actor_asset else item.media_path, attempt=0
        )
        if manual_candidate_id(selected) != request.candidate_id:
            raise RequestError(status_code=409, message="所选候选已变化，请刷新")
        await approve_candidate(
            user_id,
            selected,
            ("goal", "quality") if result.understanding.asset_kind == "scene" else ("identity", "goal", "quality"),
            request,
            db=db,
        )
        context.presentation_review = selected.review
        work = ImportWork(
            id=job_id,
            user_id=user_id,
            visual_id=item.id,
            expected_version=job.expected_version,
            context=context,
            result=result,
            snapshot=snapshot,
            media_kind=item.media_kind,
            media_path=snapshot.source.original_path or item.media_path,
            duration_ms=item.duration_ms,
        )
        update_pending_import_review(work)
        job.context_json, job.result_json = context.model_dump(mode="json"), result.model_dump(mode="json")
        unresolved = any(entry.status not in {"created", "reused", "skipped", "undone"} for entry in result.components)
        job.state, job.stage, job.error = (
            ("paused", "review_pending" if unresolved else "confirmation", None)
            if context.pending_application
            else ("queued", "organize", None)
        )
        await notify_visuals_updated(db, user_id)
        return ManualAction(
            restart_job_id=job_id if not context.pending_application else None,
            audit_context={"context_json": original_context},
            result_snapshot={"context_json": context.model_dump(mode="json")},
        )
    component = result.components[request.slot - 1]
    if component.status in {"created", "reused", "skipped", "undone"} or component.observation.kind == "action":
        raise RequestError(status_code=409, message="这条素材已经完成或不属于图片候选")
    key = component.observation.key
    chain = context.image_chains.get(key)
    if request.action == "approve":
        if chain:
            selected = await approve_image_chain(
                user_id,
                chain,
                request.model_copy(update={"slot": 0}),
                db=db,
                required=("goal", "quality")
                if component.observation.kind == "scene"
                else ("identity", "goal", "quality"),
            )
        else:
            selected = MediaCandidate(path=component.preview_path or "", attempt=0)
            if manual_candidate_id(selected) != request.candidate_id:
                raise RequestError(status_code=409, message="所选候选已变化，请刷新")
            await approve_candidate(
                user_id,
                selected,
                ("goal", "quality") if component.observation.kind == "scene" else ("identity", "goal", "quality"),
                request,
                db=db,
            )
        component.preview_path, component.candidate_verdict = selected.path, "pass"
        context.manual_reviews[key] = selected.review
        component.reason = "用户人工放行"
    else:
        context.image_chains[key] = regenerate_image_chain(chain) if chain and chain.inputs else ImageChainState()
        context.manual_reviews.pop(key, None)
        component.preview_path, component.candidate_verdict, component.creation_method = None, None, "generated"
    operations = import_operations(job.selections_json)
    previous = next((entry for entry in operations if entry.key == key), None)
    operation = (
        previous.model_copy(update={"state": "queued", "command": "retry"})
        if previous and request.action == "approve"
        else ImportOperation(key=key, command="retry", notes=component.notes)
    )
    job.selections_json = [entry.model_dump(mode="json") for entry in operations if entry.key != key] + [
        operation.model_dump(mode="json")
    ]
    component.operation_stage = "organize"
    component.revision += 1
    context.pending_application, context.presentation_review = None, None
    job.context_json, job.result_json = context.model_dump(mode="json"), result.model_dump(mode="json")
    job.state, job.stage, job.error = "queued", "organize", None
    await notify_visuals_updated(db, user_id)
    return ManualAction(
        restart_job_id=job_id,
        audit_context={"context_json": original_context},
        result_snapshot={"context_json": context.model_dump(mode="json")},
    )
