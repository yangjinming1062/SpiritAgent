"""原件分析任务的持久状态、冻结上下文和事务结果；后台执行由 workflow 持有。"""

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import enqueue_asset_cleanup
from features.jobs.service import RETAINED_JOB_ASSET_ROLE, bind_job_assets, replace_job_assets
from features.media.character_images import ImageChainState
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.review_contracts import VisualReview

from .import_operations import (
    ImportOperation,
    import_analysis_state,
    import_operations,
    import_result_view,
    resume_material_operations,
)
from .models import VisualImportJob
from .repository import (
    VisualNotFoundError,
    VisualStateError,
    notify_visuals_updated,
    require_presentation_row,
    visual_item,
)
from .schemas import (
    ActionMaterialRef,
    ComboMaterialRef,
    ImportAnalysisRequest,
    ImportAnalysisResult,
    ImportAnalysisView,
    ImportComponent,
    ImportCreatedMaterial,
    ImportSelection,
    SceneMediaRef,
    VisualAsset,
    VisualSnapshot,
)


class ImportFrameRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(ge=0)
    path: str


class ImportCatalogCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["outfit", "scene", "action"]
    material_id: ResourceId
    name: str
    description: str
    path: str | None = None
    outfit: ComboMaterialRef | None = None
    scene: SceneMediaRef | None = None
    action: ActionMaterialRef | None = None


class ImportContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_key: str = ""
    calls: int = 0
    language: str = "zh"
    user_notes: str = ""
    notes_initialized: bool = False
    correction_notes: str = ""
    identity: VisualIdentitySnapshot | None = None
    identity_path: str | None = None
    identity_hash: str | None = None
    portrait_path: str | None = None
    portrait_hash: str | None = None
    frames: list[ImportFrameRef] = Field(default_factory=list)
    selected_materials: list[ImportCatalogCandidate] = Field(default_factory=list)
    created_materials: list[ImportCreatedMaterial] = Field(default_factory=list)
    actor_asset: VisualAsset | None = None
    transparent_video: bool = False
    animated_image: bool = False
    presentation_review: VisualReview | None = None
    manual_reviews: dict[str, VisualReview] = Field(default_factory=dict)
    pending_application: dict | None = None
    timing_reliable: bool = True
    sample_duration_ms: int | None = None
    image_chains: dict[str, ImageChainState] = Field(default_factory=dict)


class ImportWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    user_id: int
    visual_id: int
    expected_version: int
    context: ImportContext
    result: ImportAnalysisResult | None
    snapshot: VisualSnapshot
    media_kind: Literal["image", "video"]
    media_path: str
    duration_ms: int | None
    active_operation: ImportOperation | None = None
    candidates: list[ImportCatalogCandidate] = Field(default_factory=list)
    review_only: bool = False

    @property
    def active_component_key(self) -> str | None:
        return self.active_operation.key if self.active_operation else None

    @property
    def active_selection(self) -> ImportSelection | None:
        operation = self.active_operation
        if operation is None or operation.command == "organize":
            return None
        return ImportSelection.model_validate(operation.model_dump(exclude={"id", "state", "stage"}))


def import_job_view(job: VisualImportJob) -> ImportAnalysisView:
    result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
    if result is not None:
        result = import_result_view(
            result,
            ImportContext.model_validate(job.context_json).image_chains,
            import_operations(job.selections_json),
            job.state,
        )
    return ImportAnalysisView(
        id=job.id,
        state=import_analysis_state(job.state, job.stage),
        stage=job.stage,
        revision=job.revision,
        error=job.error,
        result=result,
        updated_at=job.updated_at,
    )


async def ensure_import_job(user_id: int, visual_id: int) -> ImportAnalysisView | None:
    async with SESSION_LOCAL() as db:
        await require_presentation_row(db, user_id, lock=True)
        item = await visual_item(db, user_id, visual_id)
        job = await db.scalar(
            select(VisualImportJob).where(VisualImportJob.user_id == user_id, VisualImportJob.visual_id == visual_id)
        )
        if job is None and VisualSnapshot.model_validate(item.snapshot_json).source.kind != "analysis":
            return None
        if job is None:
            job = VisualImportJob(
                user_id=user_id,
                visual_id=visual_id,
                expected_version=item.version,
                original_hash=VisualSnapshot.model_validate(item.snapshot_json).source.original_content_hash,
                state="queued",
                stage="analyze",
            )
            db.add(job)
            await db.flush()
            await bind_job_assets(db, user_id, job.id, {item.media_path}, RETAINED_JOB_ASSET_ROLE)
            await notify_visuals_updated(db, user_id)
        await db.commit()
        return import_job_view(job)


async def command_import_job(user_id: int, visual_id: int, body: ImportAnalysisRequest) -> ImportAnalysisView:
    await ensure_import_job(user_id, visual_id)
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.user_id == user_id, VisualImportJob.visual_id == visual_id)
            .with_for_update()
        )
        if job is None:
            raise VisualNotFoundError("这份影像没有上传分析任务")
        if (
            body.command not in {"cancel", "apply"}
            and body.expected_revision is not None
            and job.revision != body.expected_revision
        ):
            raise VisualStateError("分析状态已更新，请刷新后重试")
        context = ImportContext.model_validate(job.context_json)
        if (
            body.command == "retry"
            and job.stage == "review_pending"
            and (
                context.presentation_review is not None
                and context.presentation_review.verdict == "review"
                or any(chain.can_retry_goal_review() for chain in context.image_chains.values())
            )
        ):
            raise VisualStateError("原候选已保留，请查看候选后采用或重新生成")
        result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
        operations = import_operations(job.selections_json)
        if body.command == "cancel":
            if job.state not in {"queued", "running", "paused"}:
                raise VisualStateError("分析已经结束")
            pause_import_operations(job)
            job.state, job.stage, job.error = "cancelled", "cancelled", "处理已取消，原件和已完成结果保留"
        elif body.command == "apply":
            context.presentation_review = None
            context.pending_application = None
            if result is None or not body.selections:
                raise VisualStateError("请先完成内容识别并选择素材")
            components = {entry.observation.key: entry for entry in result.components}
            suspended = job.state == "cancelled" or (job.state == "paused" and job.stage != "confirmation")
            if len({entry.key for entry in body.selections}) != len(body.selections) or any(
                entry.key not in components for entry in body.selections
            ):
                raise VisualStateError("所选识别条目已变化")
            for selection in body.selections:
                component = components[selection.key]
                pending = next(
                    (entry for entry in operations if entry.key == selection.key and entry.state != "failed"), None
                )
                if pending is not None:
                    if (pending.command, pending.material_id, pending.notes) == (
                        selection.command,
                        selection.material_id,
                        selection.notes.strip() or (body.notes or "").strip(),
                    ):
                        continue
                    raise VisualStateError("这条素材已有待处理操作，请等待或取消")
                if selection.expected_component_revision is not None:
                    if component.revision != selection.expected_component_revision:
                        raise VisualStateError("这条素材已更新，请刷新后重试")
                elif body.expected_revision is not None and job.revision != body.expected_revision:
                    raise VisualStateError("分析状态已更新，请刷新后重试")
                operations = [entry for entry in operations if entry.key != selection.key]
                operation = ImportOperation(**selection.model_dump())
                operation.notes = selection.notes.strip() or (body.notes or "").strip()
                if suspended:
                    operation.state = "paused"
                operations.append(operation)
                component.revision += 1
            job.selections_json = [entry.model_dump(mode="json") for entry in operations]
            job.result_json = result.model_dump(mode="json")
            if job.state != "running" and not suspended:
                job.state, job.stage, job.error = "queued", "organize", None
                job.cancel_requested = False
        else:
            if job.state in {"queued", "running"}:
                if job.state == "queued" and body.command == "retry" and body.expected_revision is None:
                    return import_job_view(job)
                raise VisualStateError("处理正在进行，请等待或取消")
            if body.command == "reanalyze":
                if any(
                    chain.phase == "submitting" or chain.stop_reason == "result_unknown"
                    for chain in context.image_chains.values()
                ):
                    raise VisualStateError("图片生成结果尚待核对，请先继续原任务")
                if any(chain.phase in {"storing", "evaluating"} for chain in context.image_chains.values()):
                    raise VisualStateError("素材图片尚未处理完成，请先继续原任务")
                # 重新识别沿用用户已提交的修正说明，只丢弃被取代的分析产物。
                context = ImportContext(
                    created_materials=context.created_materials,
                    user_notes=context.user_notes,
                    notes_initialized=context.notes_initialized,
                    correction_notes=context.correction_notes,
                )
                job.result_json, job.selections_json = None, []
                item = await visual_item(db, user_id, visual_id)
                original = VisualSnapshot.model_validate(item.snapshot_json).source.original_path or item.media_path
                await sync_import_assets(db, user_id, job.id, context, None, original)
            else:
                if result is not None:
                    operations = resume_material_operations(result, operations, context.image_chains)
                job.selections_json = [entry.model_dump(mode="json") for entry in operations]
                if result is not None:
                    job.result_json = result.model_dump(mode="json")
            job.state, job.stage, job.error = "queued", "organize" if job.result_json else "analyze", None
            job.cancel_requested = False
            if body.notes is not None:
                context.correction_notes = body.notes.strip()
        job.context_json = context.model_dump(mode="json")
        if body.command == "apply" and job.state != "running":
            item = await visual_item(db, user_id, visual_id)
            original = VisualSnapshot.model_validate(item.snapshot_json).source.original_path or item.media_path
            await sync_import_assets(db, user_id, job.id, context, result, original)
        await db.flush()
        await notify_visuals_updated(db, user_id)
        await db.commit()
        return import_job_view(job)


def pause_import_operations(job: VisualImportJob) -> None:
    operations = import_operations(job.selections_json)
    result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
    for operation in operations:
        if operation.state in {"queued", "running"}:
            operation.state = "paused"
            if result is not None:
                component = next(entry for entry in result.components if entry.observation.key == operation.key)
                component.revision += 1
    job.selections_json = [entry.model_dump(mode="json") for entry in operations]
    if result is not None:
        job.result_json = result.model_dump(mode="json")


async def claim_import_job(user_id: int, job_id: int) -> ImportWork | None:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == job_id, VisualImportJob.user_id == user_id)
            .with_for_update()
        )
        if job is None or job.state != "queued":
            return None
        item = await visual_item(db, user_id, job.visual_id)
        context = ImportContext.model_validate(job.context_json)
        context.execution_key = uuid4().hex
        if not context.notes_initialized:
            context.user_notes = item.description
            context.notes_initialized = True
        job.state, job.context_json = "running", context.model_dump(mode="json")
        await notify_visuals_updated(db, user_id)
        await db.commit()
        snapshot = VisualSnapshot.model_validate(item.snapshot_json)
        return ImportWork(
            id=job.id,
            user_id=user_id,
            visual_id=item.id,
            expected_version=item.version,
            context=context,
            result=ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None,
            snapshot=snapshot,
            media_kind=item.media_kind,
            media_path=snapshot.source.original_path,
            duration_ms=snapshot.source.original_duration_ms or item.duration_ms,
        )


async def guard_import_work(work: ImportWork, *, paid: bool = False) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
            .with_for_update()
        )
        if job is None or job.state != "running" or job.context_json.get("execution_key") != work.context.execution_key:
            raise VisualStateError("分析任务已取消或更新")
        if paid:
            context = ImportContext.model_validate(job.context_json)
            if context.calls >= 48:
                raise VisualStateError("本次分析已达到调用上限，请检查结果后明确选择重新分析")
            context.calls += 1
            job.context_json = context.model_dump(mode="json")
            work.context.calls = context.calls
        await db.commit()


async def take_import_operation(work: ImportWork) -> ImportComponent | None:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
            .with_for_update()
        )
        require_import_execution(job, work)
        operations = import_operations(job.selections_json)
        result = ImportAnalysisResult.model_validate(job.result_json)
        operation = next((entry for entry in operations if entry.state == "queued"), None)
        if operation is None:
            work.active_operation = None
            return None
        component = next(entry for entry in result.components if entry.observation.key == operation.key)
        if operation.command != "organize":
            component.notes = operation.notes
        operation.state = "running"
        component.failure_code = None
        component.failure_summary = ""
        component.revision += 1
        operation.stage = "organize"
        context = work.context.model_copy(
            update={
                "calls": max(work.context.calls, job.context_json.get("calls", 0)),
                "presentation_review": None,
                "pending_application": None,
            }
        )
        job.context_json = context.model_dump(mode="json")
        job.result_json = result.model_dump(mode="json")
        job.selections_json = [entry.model_dump(mode="json") for entry in operations]
        await notify_visuals_updated(db, work.user_id)
        await db.commit()
        work.active_operation, work.result, work.context = operation, result, context
        return component


def require_import_execution(job: VisualImportJob | None, work: ImportWork) -> None:
    if job is None or job.state != "running" or job.context_json.get("execution_key") != work.context.execution_key:
        raise VisualStateError("分析任务已取消或更新")


def merge_import_progress(
    job: VisualImportJob, work: ImportWork, stage: str, *, finish: bool = False
) -> ImportAnalysisResult | None:
    if work.result is None:
        return None
    result = work.result.model_copy(deep=True)
    operations = import_operations(job.selections_json)
    if job.result_json is None:
        for component in result.components:
            operations.append(ImportOperation(key=component.observation.key, command="organize"))
    elif work.active_component_key is not None:
        stored = ImportAnalysisResult.model_validate(job.result_json)
        component = next(entry for entry in result.components if entry.observation.key == work.active_component_key)
        previous = next(entry for entry in stored.components if entry.observation.key == work.active_component_key)
        component.revision = previous.revision + 1
        operation = next((entry for entry in operations if entry.id == work.active_operation.id), None)
        if operation is None or operation.state != "running":
            raise VisualStateError("素材操作已取消或更新")
        operation.stage = stage
        if finish:
            if component.status in {"failed", "reviewing"}:
                operation.state = "paused" if component.status == "reviewing" else "failed"
            else:
                operations.remove(operation)
        result.components = [
            component if entry.observation.key == work.active_component_key else entry for entry in stored.components
        ]
    else:
        result = ImportAnalysisResult.model_validate(job.result_json)
    job.result_json = result.model_dump(mode="json")
    job.selections_json = [entry.model_dump(mode="json") for entry in operations]
    return result


async def sync_import_assets(
    db: AsyncSession,
    user_id: int,
    job_id: int,
    context: ImportContext,
    result: ImportAnalysisResult | None,
    original_path: str,
) -> None:
    """导入资料随分析记录保留，引用切换与业务结果共同提交。"""
    paths = {original_path, *(entry.path for entry in context.frames)}
    if context.presentation_review and context.presentation_review.evidence:
        paths |= {entry.path for entry in context.presentation_review.evidence.images}
    for review in context.manual_reviews.values():
        if review.evidence:
            paths |= {entry.path for entry in review.evidence.images}
    paths |= {entry for entry in (context.identity_path, context.portrait_path) if entry}
    paths |= {entry.path for entry in context.selected_materials if entry.path}
    for chain in context.image_chains.values():
        chain_paths = chain.stored_paths()
        if chain.pending_path and resolve_asset_reference(chain.pending_path) is None:
            chain_paths.discard(chain.pending_path)
        paths |= chain_paths
    if result:
        paths |= {entry.preview_path for entry in result.components if entry.preview_path}
    if context.actor_asset:
        asset = context.actor_asset
        paths |= {entry for entry in (asset.media_path, asset.poster_path) if entry}
        if asset.body:
            paths |= {
                entry
                for entry in (asset.body.mask_path, asset.body.first_frame_path, asset.body.last_frame_path)
                if entry
            }
    released = await replace_job_assets(db, user_id, job_id, paths, RETAINED_JOB_ASSET_ROLE)
    await enqueue_asset_cleanup(db, user_id, released)


def finish_import_component(context: ImportContext, component: ImportComponent) -> None:
    """已完成条目只保留采用候选及其检查依据；未决请求仍需原链恢复。"""
    if component.status not in {"created", "reused", "skipped", "undone"}:
        return
    key = component.observation.key
    chain = context.image_chains.get(key)
    if chain is None:
        return
    if (
        chain.pending_urls
        or chain.pending_path
        or chain.phase in {"submitting", "storing", "evaluating"}
        or chain.stop_reason == "result_unknown"
    ):
        return
    if component.status == "created":
        candidate = next((entry for entry in chain.candidates if entry.path == component.preview_path), None)
        if candidate is None:
            context.image_chains.pop(key, None)
            return
        chain.candidates = [candidate]
        context.manual_reviews.pop(key, None)
        return
    component.preview_path = None
    component.candidate_verdict = None
    component.creation_method = None
    context.manual_reviews.pop(key, None)
    context.image_chains.pop(key, None)


async def save_import_progress(
    work: ImportWork,
    stage: str,
    *,
    adopt: Callable[[AsyncSession], Awaitable[None]] | None = None,
    finish: bool = False,
) -> None:
    component = (
        next((entry for entry in work.result.components if entry.observation.key == work.active_component_key), None)
        if work.result
        else None
    )
    if work.active_operation is not None:
        work.active_operation.stage = "save_material" if adopt is not None else stage
    previous = component.model_copy(deep=True) if component is not None else None
    previous_context = work.context.model_copy(deep=True) if adopt is not None or finish else None
    try:
        async with SESSION_LOCAL() as db:
            job = await db.scalar(
                select(VisualImportJob)
                .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
                .with_for_update()
            )
            require_import_execution(job, work)
            if adopt is not None:
                await adopt(db)
            calls = max(work.context.calls, job.context_json.get("calls", 0))
            # 条目收尾先裁剪引用，结果复制与落库随之只带采用候选。
            if finish and component is not None:
                finish_import_component(work.context, component)
            result = merge_import_progress(job, work, stage, finish=finish)
            job.context_json = work.context.model_copy(update={"calls": calls}).model_dump(mode="json")
            job.stage = stage
            await sync_import_assets(db, work.user_id, job.id, work.context, result, work.media_path)
            await notify_visuals_updated(db, work.user_id)
            await db.commit()
    except BaseException:
        if previous_context is not None:
            work.context = previous_context
        if component is not None and previous is not None:
            for field in type(component).model_fields:
                setattr(component, field, getattr(previous, field))
        raise
    work.context.calls = calls
    if result is not None and component is not None:
        saved = next(entry for entry in result.components if entry.observation.key == component.observation.key)
        component.revision = saved.revision
        result.components = [component if entry is saved else entry for entry in result.components]
    work.result = result
    if finish:
        work.active_operation = None


async def fail_import_work(work: ImportWork, message: str, *, paused: bool = False) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
            .with_for_update()
        )
        if (
            job is not None
            and job.state == "running"
            and job.context_json.get("execution_key") == work.context.execution_key
        ):
            pause_import_operations(job)
            pending_review = (
                work.context.presentation_review is not None and work.context.presentation_review.verdict == "review"
            )
            job.state, job.stage, job.error = (
                "paused" if paused or pending_review else "failed",
                "review_pending" if pending_review else "interrupted" if paused else job.stage,
                message,
            )
            await notify_visuals_updated(db, work.user_id)
            await db.commit()


async def recover_import_jobs(user_id: int | None = None) -> list[tuple[int, int]]:
    async with SESSION_LOCAL() as db:
        query = (
            select(VisualImportJob)
            .where(
                or_(
                    VisualImportJob.state.in_(("running", "queued")),
                    and_(VisualImportJob.state == "paused", VisualImportJob.stage == "interrupted"),
                )
            )
            .with_for_update()
        )
        if user_id is not None:
            query = query.where(VisualImportJob.user_id == user_id)
        jobs = list(await db.scalars(query))
        queued = []
        for job in jobs:
            context = ImportContext.model_validate(job.context_json)
            interrupted = job.state == "running" or (job.state == "paused" and job.stage == "interrupted")
            if interrupted:
                pause_import_operations(job)
                if (
                    job.stage in {"presentation_review", "reviewing"}
                    or (context.presentation_review is not None and context.presentation_review.verdict == "review")
                    or any(chain.can_retry_goal_review() for chain in context.image_chains.values())
                ):
                    context.execution_key = ""
                    job.context_json = context.model_dump(mode="json")
                    job.state, job.stage, job.error = (
                        "paused",
                        "review_pending",
                        "检查已中断，原件与证据已保留，等待选择候选",
                    )
                    await notify_visuals_updated(db, job.user_id)
                    continue
                result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
                operations = import_operations(job.selections_json)
                # 只恢复被中断的操作；已经失败的条目等待显式重试。
                unresolved = any(
                    chain.phase == "submitting" or chain.stop_reason == "result_unknown"
                    for chain in context.image_chains.values()
                )
                if not unresolved and context.calls < 48:
                    if result is not None:
                        operations = resume_material_operations(
                            result, operations, context.image_chains, include_failed=False
                        )
                        job.result_json = result.model_dump(mode="json")
                        job.selections_json = [entry.model_dump(mode="json") for entry in operations]
                    job.state, job.stage, job.error = "queued", "organize" if result else "analyze", None
                elif interrupted:
                    job.state, job.stage, job.error = "paused", "interrupted", "原任务需要核对，已保留原件与处理进度"
                await notify_visuals_updated(db, job.user_id)
            if job.state == "queued":
                queued.append((job.user_id, job.id))
        await db.commit()
        return queued
