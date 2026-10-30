"""原件分析任务的持久状态、冻结上下文和事务结果；后台执行由 workflow 持有。"""

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import bind_job_assets
from features.media.identity_contracts import CharacterCardSnapshot

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


def import_job_view(job: VisualImportJob) -> ImportAnalysisView:
    return ImportAnalysisView(
        id=job.id,
        state=job.state,
        stage=job.stage,
        revision=job.revision,
        error=job.error,
        result=ImportAnalysisResult.model_validate(job.result_json) if job.result_json else None,
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
        if body.command != "cancel" and body.expected_revision is not None and job.revision != body.expected_revision:
            raise VisualStateError("分析状态已更新，请刷新后重试")
        if body.command == "cancel":
            if job.state not in {"queued", "running", "paused"}:
                raise VisualStateError("分析已经结束")
            job.state, job.stage, job.error = "cancelled", "cancelled", "用户已取消分析，原件仍保留"
        else:
            if job.state in {"queued", "running"}:
                if job.state == "queued" and body.command == "retry" and body.expected_revision is None:
                    if body.notes is not None:
                        context = ImportContext.model_validate(job.context_json)
                        context.correction_notes = body.notes.strip()
                        job.context_json = context.model_dump(mode="json")
                    await db.commit()
                    return import_job_view(job)
                raise VisualStateError("分析正在进行，请等待或取消")
            if body.command == "apply":
                if not job.result_json or not body.selections:
                    raise VisualStateError("请先完成内容分析并选择要修正的素材")
                result = ImportAnalysisResult.model_validate(job.result_json)
                keys = {entry.observation.key for entry in result.components}
                if len({entry.key for entry in body.selections}) != len(body.selections) or any(
                    entry.key not in keys for entry in body.selections
                ):
                    raise VisualStateError("所选识别条目已变化")
                context = ImportContext.model_validate(job.context_json)
                context.history.append(result)
                job.context_json = context.model_dump(mode="json")
                job.selections_json = [entry.model_dump(mode="json") for entry in body.selections]
            elif body.command == "reanalyze":
                previous = ImportContext.model_validate(job.context_json)
                history = previous.history
                if job.result_json:
                    history.append(ImportAnalysisResult.model_validate(job.result_json))
                job.result_json = None
                job.context_json = ImportContext(
                    history=history,
                    user_notes=previous.user_notes,
                    notes_initialized=previous.notes_initialized,
                    correction_notes=previous.correction_notes,
                ).model_dump(mode="json")
                job.selections_json = []
            elif job.state == "succeeded":
                raise VisualStateError("分析已完成，修改关联或重新分析时请明确选择操作")
            job.state, job.stage, job.error = "queued", "organize" if job.result_json else "analyze", None
            job.cancel_requested = False
            if body.notes is not None:
                context = ImportContext.model_validate(job.context_json)
                context.correction_notes = body.notes.strip()
                job.context_json = context.model_dump(mode="json")
        await db.flush()
        await notify_visuals_updated(db, user_id)
        await db.commit()
        return import_job_view(job)


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
            selections=[ImportSelection.model_validate(entry) for entry in job.selections_json],
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


async def save_import_progress(
    work: ImportWork,
    stage: str,
    *,
    adopt: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        job = await db.scalar(
            select(VisualImportJob)
            .where(VisualImportJob.id == work.id, VisualImportJob.user_id == work.user_id)
            .with_for_update()
        )
        if job is None or job.state != "running" or job.context_json.get("execution_key") != work.context.execution_key:
            raise VisualStateError("分析任务已取消或更新")
        if adopt is not None:
            await adopt(db)
        work.context.calls = max(work.context.calls, job.context_json.get("calls", 0))
        job.context_json = work.context.model_dump(mode="json")
        job.result_json = work.result.model_dump(mode="json") if work.result else None
        job.stage = stage
        paths = {entry.path for entry in work.context.frames}
        paths |= {entry for entry in (work.context.identity_path, work.context.portrait_path) if entry}
        paths |= {entry.path for entry in work.context.candidates if entry.path}
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
