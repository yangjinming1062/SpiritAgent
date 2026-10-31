"""制作工作项与生活步骤共用业务事务；一次制作可以兑现多个创作请求。"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from features.life.schemas import LifeEventInput, LifeStepRequest
from features.life.use_cases import bind_step_in_session, update_business_steps_in_session

from .models import VisualJob
from .repository import visual_progress


async def bind_creation_step(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    request: LifeStepRequest | None,
) -> None:
    if request is None:
        return
    progress = visual_progress(job)
    if progress is not None and progress.source == "base_preparation":
        progress.source = "autonomous"
        job.checkpoint = progress.model_dump(mode="json")
    await bind_step_in_session(
        db,
        user_id,
        request,
        business_ref={
            "job_id": job.id,
            "generation_id": progress.generation_id if progress is not None else None,
        },
    )
    await settle_visual_steps(db, user_id, job)


async def settle_visual_steps(
    db: AsyncSession,
    user_id: int,
    job: VisualJob,
    *,
    result_ref: dict | None = None,
    additional_events: Sequence[LifeEventInput] = (),
) -> None:
    if job.state not in {"succeeded", "failed", "cancelled", "unknown"}:
        return
    progress = visual_progress(job)
    if progress is not None and progress.source == "base_preparation":
        return
    completed = job.state == "succeeded"
    failure_code = None if completed else (progress.failure_reason if progress is not None else None)
    await update_business_steps_in_session(
        db,
        user_id,
        kind="visual.generate",
        business_ref={"job_id": job.id},
        state="completed" if completed else job.state,
        result_ref={
            "job_id": job.id,
            "visual_id": job.result_visual_id,
            "visual_version_id": job.result_version_id,
            **(result_ref or {}),
        },
        error=None if completed else job.error,
        failure_code=failure_code,
        next_step="结合生活情境决定是否呈现" if completed else "继续接收原成品或核对结果",
        additional_events=additional_events,
    )


async def restart_visual_steps(db: AsyncSession, user_id: int, job: VisualJob) -> None:
    await update_business_steps_in_session(
        db,
        user_id,
        kind="visual.generate",
        business_ref={"job_id": job.id},
        state="running",
    )
