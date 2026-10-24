"""制作工作项与生活步骤共用业务事务；一次制作可以兑现多个创作请求。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.life.schemas import LifeStepRequest
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
    await bind_step_in_session(
        db,
        user_id,
        request,
        business_ref={
            "visual_job_id": job.id,
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
    generation_id: str | None = None,
) -> None:
    if job.state not in {"succeeded", "failed", "cancelled", "unknown"}:
        return
    progress = visual_progress(job)
    completed = job.state == "succeeded"
    await update_business_steps_in_session(
        db,
        user_id,
        kind="visual.generate",
        business_ref={"visual_job_id": job.id},
        idempotency_key=generation_id or (progress.generation_id if progress is not None else None),
        state="completed" if completed else job.state,
        result_ref={"visual_job_id": job.id, "visual_id": job.result_visual_id, **(result_ref or {})},
        error=None if completed else job.error,
        next_step="结合生活情境决定是否呈现" if completed else "确认是否重试影像制作",
    )


async def restart_visual_steps(db: AsyncSession, user_id: int, job: VisualJob) -> None:
    progress = visual_progress(job)
    await update_business_steps_in_session(
        db,
        user_id,
        kind="visual.generate",
        business_ref={"visual_job_id": job.id},
        idempotency_key=progress.generation_id if progress is not None else None,
        state="running",
    )
