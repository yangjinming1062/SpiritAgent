"""定时任务归属的到期查询与事务内 CAS 推进。"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, bindparam, delete, or_, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from .cron_jobs import compute_next_run_at
from .models import CronJob


@dataclass(frozen=True)
class DueJob:
    id: int
    user_id: int
    name: str
    schedule: str
    next_run_at: datetime
    prompt: str
    one_shot: bool
    kind: str
    conversation_id: int | None
    expires_at: datetime | None


async def due_jobs(
    db: AsyncSession,
    now: datetime,
    online_user_ids: list[int],
    active_user_ids: list[int],
    *,
    limit: int,
) -> list[DueJob]:
    """读取到期 job；未过期的窗口型问候只在用户在线时入选，避免离线等待项堵住全局批次。"""
    expiring_scope = or_(
        CronJob.expires_at.is_(None),
        CronJob.expires_at <= now,
    )
    if online_user_ids:
        expiring_scope = or_(expiring_scope, CronJob.user_id.in_(online_user_ids))
    rows = (
        await db.execute(
            select(
                CronJob.id,
                CronJob.user_id,
                CronJob.name,
                CronJob.schedule,
                CronJob.next_run_at,
                CronJob.prompt,
                CronJob.one_shot,
                CronJob.kind,
                CronJob.conversation_id,
                CronJob.expires_at,
            )
            .where(
                CronJob.user_id.in_(active_user_ids),
                CronJob.is_paused.is_(False),
                CronJob.next_run_at.is_not(None),
                CronJob.next_run_at <= now,
                expiring_scope,
            )
            .order_by(CronJob.next_run_at, CronJob.id)
            .limit(limit),
        )
    ).all()
    return [DueJob(*row) for row in rows]


async def advance_due_jobs(
    db: AsyncSession,
    due_jobs: list[DueJob],
    now: datetime,
    active_users: set[int],
) -> list[DueJob]:
    next_runs = {job.id: compute_next_run_at(job.schedule, now) for job in due_jobs if not job.one_shot}
    won: set[int] = set()
    # 内容或轨别修改未必改变调度游标；持有用户锁后核对快照，防止撤销的旧意图再次交接。
    current_jobs: dict[int, CronJob] = {
        job.id: job
        for job in (await db.execute(select(CronJob).where(CronJob.id.in_([job.id for job in due_jobs])))).scalars()
    }
    due_jobs = [
        job
        for job in due_jobs
        if (current := current_jobs.get(job.id)) is not None
        and job.user_id in active_users
        and not current.is_paused
        and all(getattr(current, key) == value for key, value in asdict(job).items())
    ]
    recurring = [job for job in due_jobs if not job.one_shot]
    one_shots = [job for job in due_jobs if job.one_shot]
    if recurring:
        case_clauses = " ".join(f"WHEN :id_{i} THEN :next_{i}" for i in range(len(recurring)))
        stmt = text(
            f"UPDATE cron_jobs SET "
            f"next_run_at = CASE id {case_clauses} END, "
            f"is_paused = (CASE id {case_clauses} END) IS NULL "
            f"WHERE (id, next_run_at, schedule) IN :match "
            f"RETURNING id",
        ).bindparams(
            bindparam("match", expanding=True),
            *(bindparam(f"next_{i}", type_=DateTime(timezone=True)) for i in range(len(recurring))),
        )
        params: dict[str, Any] = {
            "match": [(j.id, j.next_run_at, j.schedule) for j in recurring],
        }
        for i, j in enumerate(recurring):
            params[f"id_{i}"] = j.id
            params[f"next_{i}"] = next_runs[j.id]
        res = await db.execute(stmt, params)
        won.update(r[0] for r in res.all())
    if one_shots:
        # 先认领，待意图引用落库后再删除；否则新意图会引用已删除的源任务。
        stmt = (
            update(CronJob)
            .where(
                tuple_(CronJob.id, CronJob.next_run_at).in_(
                    bindparam("match", expanding=True),
                ),
            )
            .values(next_run_at=None)
            .returning(CronJob.id)
        )
        res = await db.execute(
            stmt,
            {"match": [(j.id, j.next_run_at) for j in one_shots]},
        )
        won.update(r[0] for r in res.all())
    return [job for job in due_jobs if job.id in won and (job.one_shot or next_runs[job.id] is not None)]


async def delete_consumed_one_shots(db: AsyncSession, jobs: list[DueJob]) -> None:
    ids = [job.id for job in jobs if job.one_shot]
    if ids:
        await db.execute(delete(CronJob).where(CronJob.id.in_(ids), CronJob.next_run_at.is_(None)))


async def delete_expired_jobs(db: AsyncSession, job_ids: set[int], now: datetime) -> None:
    await db.execute(delete(CronJob).where(CronJob.id.in_(job_ids), CronJob.expires_at <= now))
