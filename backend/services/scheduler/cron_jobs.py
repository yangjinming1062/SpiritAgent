from datetime import datetime
from typing import Any

from components import get_logger, session_scope, utc_now
from croniter import croniter
from modules.auth import User
from modules.conversation import Conversation
from modules.scheduler import CronJob
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.conversation import STANDARD_KIND

logger = get_logger(__name__)

SPECIAL_CRON_KIND = "special"
STANDARD_CRON_KIND = "standard"
CRON_KINDS = frozenset({SPECIAL_CRON_KIND, STANDARD_CRON_KIND})

_JOB_IMMUTABLE_FIELDS = frozenset({"id", "user_id", "conversation_id"})
_SCHEDULE_KEYS = ("schedule", "is_paused")
MAX_ACTIVE_CRON_JOBS = 10


def _validate_kind(kind: str) -> str:
    normalized = (kind or "").strip().lower()
    if normalized not in CRON_KINDS:
        raise ValueError("kind must be one of: special, standard")
    return normalized


def _conversation_title(name: str) -> str:
    return f"定时任务 · {name}"


def _compute_next_run_at(schedule: str, base: datetime) -> datetime | None:
    try:
        # croniter 保留 base 的 tzinfo——aware 进 aware 出。
        return croniter(schedule, base).get_next(datetime)
    except Exception as exc:  # noqa: BLE001 - croniter 的公开异常集合随版本变化，非法表达式统一转为暂停任务
        logger.error(
            "Invalid cron expression",
            extra={"schedule": schedule, "error": str(exc)},
        )
        return None


def _refresh_schedule(job: CronJob) -> None:
    next_run = _compute_next_run_at(job.schedule, utc_now())
    if next_run is None:
        job.is_paused = True
        job.next_run_at = None
    else:
        job.next_run_at = next_run


async def _lock_user_cron_jobs(db: AsyncSession, user_id: int) -> None:
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def _ensure_active_job_capacity(db: AsyncSession, user_id: int, candidate_job_id: int | None = None) -> None:
    stmt = select(func.count()).select_from(CronJob).where(CronJob.user_id == user_id, CronJob.is_paused.is_(False))
    if candidate_job_id is not None:
        stmt = stmt.where(CronJob.id != candidate_job_id)
    if (await db.execute(stmt)).scalar_one() >= MAX_ACTIVE_CRON_JOBS:
        raise ValueError(
            f"Maximum active cron jobs limit ({MAX_ACTIVE_CRON_JOBS}) reached.",
        )


async def create_job(
    user_id: int,
    prompt: str,
    schedule: str,
    name: str = "cron job",
    deliver: str = "local",
    one_shot: bool = False,
    kind: str = STANDARD_CRON_KIND,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    normalized_kind = _validate_kind(kind)
    async with session_scope() as db:
        await _lock_user_cron_jobs(db, user_id)
        job = CronJob(
            user_id=user_id,
            name=name,
            schedule=schedule,
            prompt=prompt,
            kind=normalized_kind,
            deliver=deliver,
            is_paused=False,
            one_shot=one_shot,
            expires_at=expires_at,
        )
        _refresh_schedule(job)
        if not job.is_paused:
            await _ensure_active_job_capacity(db, user_id)
        conversation_id: int | None = None
        if normalized_kind == STANDARD_CRON_KIND:
            conversation = Conversation(
                user_id=user_id,
                kind=STANDARD_KIND,
                title=_conversation_title(name),
                is_automation=True,
            )
            db.add(conversation)
            await db.flush()
            conversation_id = conversation.id
        job.conversation_id = conversation_id
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.to_dict()


async def get_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    async with session_scope() as db:
        job = (
            await db.execute(
                select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id),
            )
        ).scalar_one_or_none()
        return job.to_dict() if job else None


async def list_jobs(user_id: int, include_paused: bool = False) -> list[dict[str, Any]]:
    async with session_scope() as db:
        stmt = select(CronJob).where(CronJob.user_id == user_id)
        if not include_paused:
            stmt = stmt.where(CronJob.is_paused.is_(False))
        jobs = (await db.execute(stmt)).scalars().all()
        return [j.to_dict() for j in jobs]


async def update_job(
    user_id: int,
    job_id: int,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    if "kind" in updates:
        updates["kind"] = _validate_kind(updates["kind"])
    async with session_scope() as db:
        await _lock_user_cron_jobs(db, user_id)
        job = (
            await db.execute(
                select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id),
            )
        ).scalar_one_or_none()
        if not job:
            return None
        for key, value in updates.items():
            if key in _JOB_IMMUTABLE_FIELDS or not hasattr(job, key):
                continue
            setattr(job, key, value)
        if any(k in updates for k in _SCHEDULE_KEYS):
            _refresh_schedule(job)
        if not job.is_paused:
            await _ensure_active_job_capacity(db, user_id, job.id)
        if job.kind == STANDARD_CRON_KIND and job.conversation_id is None:
            conversation = Conversation(
                user_id=user_id,
                kind=STANDARD_KIND,
                title=_conversation_title(job.name),
                is_automation=True,
            )
            db.add(conversation)
            await db.flush()
            job.conversation_id = conversation.id
        elif job.kind == SPECIAL_CRON_KIND:
            job.conversation_id = None
        await db.commit()
        return job.to_dict()


async def pause_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return await update_job(user_id, job_id, {"is_paused": True})


async def resume_job(user_id: int, job_id: int) -> dict[str, Any] | None:
    return await update_job(user_id, job_id, {"is_paused": False})


async def remove_job(user_id: int, job_id: int) -> bool:
    async with session_scope() as db:
        job = (
            await db.execute(
                select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id),
            )
        ).scalar_one_or_none()
        if not job:
            return False
        await db.delete(job)
        await db.commit()
        return True
