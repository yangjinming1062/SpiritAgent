"""原件分析任务的持久状态、冻结上下文和事务结果；后台执行由 workflow 持有。"""

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from infrastructure.api_ids import ResourceId
from infrastructure.assets.asset_store import resolve_asset_reference
from infrastructure.database import SESSION_LOCAL
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import bind_job_assets
from features.media.character_images import ImageChainState
from features.media.identity_contracts import CharacterCardSnapshot

from .import_operations import ImportOperation, import_operations, normalize_import_result
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
    identity: CharacterCardSnapshot | None = None
    identity_path: str | None = None
    identity_hash: str | None = None
    portrait_path: str | None = None
    portrait_hash: str | None = None
    frames: list[ImportFrameRef] = Field(default_factory=list)
    candidates: list[ImportCatalogCandidate] = Field(default_factory=list)
    history: list[ImportAnalysisResult] = Field(default_factory=list)
    actor_asset: VisualAsset | None = None
    transparent_video: bool = False
    animated_image: bool = False
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
    selections: list[ImportSelection]
    snapshot: VisualSnapshot
    media_kind: Literal["image", "video"]
    media_path: str
    duration_ms: int | None
    active_operation_id: str | None = None
    active_component_key: str | None = None


def import_job_view(job: VisualImportJob) -> ImportAnalysisView:
    result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
    if result is not None:
        normalize_import_result(result, ImportContext.model_validate(job.context_json).image_chains)
        pending = {entry.key: entry for entry in import_operations(job.selections_json) if entry.state != "failed"}
        for component in result.components:
            if component.observation.key in pending:
                operation = pending[component.observation.key]
                component.operation_state = "paused" if job.state in {"paused", "cancelled"} else operation.state
    return ImportAnalysisView(
        id=job.id,
        state=job.state,
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
            await bind_job_assets(db, user_id, job.id, {item.media_path}, "original")
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
        result = ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None
        if result is not None:
            normalize_import_result(result, context.image_chains)
        operations = import_operations(job.selections_json)
        if body.command == "cancel":
            if job.state not in {"queued", "running", "paused"}:
                raise VisualStateError("分析已经结束")
            pause_import_operations(job)
            job.state, job.stage, job.error = "cancelled", "cancelled", "处理已取消，原件和已完成结果保留"
        elif body.command == "apply":
            if result is None or not body.selections:
                raise VisualStateError("请先完成内容识别并选择素材")
            components = {entry.observation.key: entry for entry in result.components}
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
                if job.state in {"paused", "cancelled"}:
                    operation.state = "paused"
                operations.append(operation)
                component.revision += 1
                component.operation_state, component.operation_stage = operation.state, "organize"
            job.selections_json = [entry.model_dump(mode="json") for entry in operations]
            job.result_json = result.model_dump(mode="json")
            if job.state not in {"running", "paused", "cancelled"}:
                job.state, job.stage, job.error = "queued", "organize", None
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
                if result is not None:
                    context.history.append(result)
                # 重新识别沿用用户已提交的修正说明，只丢弃被取代的分析产物。
                context = ImportContext(
                    history=context.history,
                    user_notes=context.user_notes,
                    notes_initialized=context.notes_initialized,
                    correction_notes=context.correction_notes,
                )
                job.result_json, job.selections_json = None, []
            else:
                for operation in operations:
                    if operation.state in {"paused", "running"}:
                        operation.state = "queued"
                        if result is not None:
                            component = next(
                                entry for entry in result.components if entry.observation.key == operation.key
                            )
                            component.operation_state = "queued"
                            component.revision += 1
                job.selections_json = [entry.model_dump(mode="json") for entry in operations]
                if result is not None:
                    job.result_json = result.model_dump(mode="json")
            job.state, job.stage, job.error = "queued", "organize" if job.result_json else "analyze", None
            job.cancel_requested = False
            if body.notes is not None:
                context.correction_notes = body.notes.strip()
            job.context_json = context.model_dump(mode="json")
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
                component.operation_state = "paused"
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
            selections=[],
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
            .where(
                VisualImportJob.id == work.id,
                VisualImportJob.user_id == work.user_id,
            )
            .with_for_update()
        )
        require_import_execution(job, work)
        operations = import_operations(job.selections_json)
        work.result = ImportAnalysisResult.model_validate(job.result_json)
        normalize_import_result(work.result, work.context.image_chains)
        operation = next((entry for entry in operations if entry.state == "queued"), None)
        if operation is None:
            work.active_component_key, work.active_operation_id, work.selections = None, None, []
            return None
        component = next(entry for entry in work.result.components if entry.observation.key == operation.key)
        if operation.command != "organize":
            work.context.history.append(work.result.model_copy(deep=True))
        work.context.correction_notes = operation.notes
        operation.state = "running"
        component.operation_state, component.operation_stage = "running", "organize"
        component.failure_code = None
        component.revision += 1
        work.active_component_key, work.active_operation_id = operation.key, operation.id
        work.selections = (
            []
            if operation.command == "organize"
            else [ImportSelection.model_validate(operation.model_dump(exclude={"id", "state"}))]
        )
        work.context.calls = max(work.context.calls, job.context_json.get("calls", 0))
        job.context_json = work.context.model_dump(mode="json")
        job.result_json = work.result.model_dump(mode="json")
        job.selections_json = [entry.model_dump(mode="json") for entry in operations]
        await notify_visuals_updated(db, work.user_id)
        await db.commit()
        return component


def require_import_execution(job: VisualImportJob | None, work: ImportWork) -> None:
    if job is None or job.state != "running" or job.context_json.get("execution_key") != work.context.execution_key:
        raise VisualStateError("分析任务已取消或更新")


def merge_import_progress(job: VisualImportJob, work: ImportWork, stage: str, *, finish: bool = False) -> None:
    if work.result is None:
        return
    operations = import_operations(job.selections_json)
    if job.result_json is None:
        for component in work.result.components:
            component.operation_state, component.operation_stage = "queued", "match"
            operations.append(ImportOperation(key=component.observation.key, command="organize"))
    elif work.active_component_key is not None:
        stored = ImportAnalysisResult.model_validate(job.result_json)
        component = next(
            entry for entry in work.result.components if entry.observation.key == work.active_component_key
        )
        previous = next(entry for entry in stored.components if entry.observation.key == work.active_component_key)
        component.operation_stage = stage
        component.revision = previous.revision + 1
        if finish:
            component.operation_state = "failed" if component.status == "failed" else "succeeded"
            operations = [
                entry for entry in operations if entry.id != work.active_operation_id or component.status == "failed"
            ]
            for entry in operations:
                if entry.id == work.active_operation_id:
                    entry.state = "failed"
        work.result.components = [
            component if entry.observation.key == work.active_component_key else entry for entry in stored.components
        ]
    else:
        work.result = ImportAnalysisResult.model_validate(job.result_json)
    job.result_json = work.result.model_dump(mode="json")
    job.selections_json = [entry.model_dump(mode="json") for entry in operations]


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
    if adopt is not None and component is not None:
        component.operation_stage = "save_material"
    previous = component.model_copy(deep=True) if component is not None else None
    try:
        async with SESSION_LOCAL() as db:
            job = await db.scalar(
                select(VisualImportJob)
                .where(
                    VisualImportJob.id == work.id,
                    VisualImportJob.user_id == work.user_id,
                )
                .with_for_update()
            )
            require_import_execution(job, work)
            if adopt is not None:
                await adopt(db)
            work.context.calls = max(work.context.calls, job.context_json.get("calls", 0))
            job.context_json = work.context.model_dump(mode="json")
            merge_import_progress(job, work, stage, finish=finish)
            job.stage = stage
            paths = {entry.path for entry in work.context.frames}
            paths |= {entry for entry in (work.context.identity_path, work.context.portrait_path) if entry}
            paths |= {entry.path for entry in work.context.candidates if entry.path}
            for chain in work.context.image_chains.values():
                chain_paths = chain.stored_paths()
                if chain.pending_path and resolve_asset_reference(chain.pending_path) is None:
                    chain_paths.discard(chain.pending_path)
                paths |= chain_paths
            if work.result:
                paths |= {entry.preview_path for entry in work.result.components if entry.preview_path}
            if work.context.actor_asset:
                asset = work.context.actor_asset
                paths |= {entry for entry in (asset.media_path, asset.poster_path) if entry}
                if asset.body:
                    paths |= {
                        entry
                        for entry in (asset.body.mask_path, asset.body.first_frame_path, asset.body.last_frame_path)
                        if entry
                    }
            await bind_job_assets(db, work.user_id, job.id, paths, "analysis")
            await notify_visuals_updated(db, work.user_id)
            await db.commit()
    except BaseException:
        if component is not None and previous is not None:
            for field in type(component).model_fields:
                setattr(component, field, getattr(previous, field))
        raise
    if finish:
        work.active_component_key, work.active_operation_id, work.selections = None, None, []


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
            job.state, job.stage, job.error = (
                "paused" if paused else "failed",
                "interrupted" if paused else job.stage,
                message,
            )
            await notify_visuals_updated(db, work.user_id)
            await db.commit()


async def recover_import_jobs(user_id: int | None = None) -> list[tuple[int, int]]:
    async with SESSION_LOCAL() as db:
        query = select(VisualImportJob).where(VisualImportJob.state.in_(("running", "queued")))
        if user_id is not None:
            query = query.where(VisualImportJob.user_id == user_id)
        jobs = list(await db.scalars(query))
        queued = []
        for job in jobs:
            if job.state == "running":
                pause_import_operations(job)
                job.state, job.stage, job.error = (
                    "paused",
                    "interrupted",
                    "分析已中断，原件与已完成结果保留，可继续分析",
                )
                await notify_visuals_updated(db, job.user_id)
            else:
                queued.append((job.user_id, job.id))
        await db.commit()
        return queued
