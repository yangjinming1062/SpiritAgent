"""任务身份和生命周期的事务内公共操作。"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from features.assets.service import asset_paths_in_session, ensure_assets_in_session
from infrastructure.job_models import JobRecord
from infrastructure.text import utc_now
from sqlalchemy import BigInteger, cast, column, delete, null, or_, select, update, values
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CTE

from .models import JobAsset, JobRequest


@dataclass(frozen=True, slots=True)
class CompletedJob:
    revision: int
    finished_at: datetime
    updated_at: datetime


def completion_cte(user_id: int, job_id: int, expected_revision: int, checkpoint: dict) -> CTE:
    """固定的任务完成片段；由拥有业务结果的语句组合执行，守卫不向调用方开放。"""
    now = utc_now()
    return (
        update(JobRecord.__table__)
        .where(
            JobRecord.id == job_id,
            JobRecord.user_id == user_id,
            JobRecord.revision == expected_revision,
            JobRecord.state.in_(("queued", "running")),
            JobRecord.cancel_requested.is_(False),
            JobRecord.cleanup_pending.is_(False),
        )
        .values(
            state="succeeded",
            stage="complete",
            revision=JobRecord.revision + 1,
            error=None,
            checkpoint=checkpoint,
            finished_at=now,
            updated_at=now,
        )
        .returning(JobRecord.id.label("job_id"), JobRecord.revision, JobRecord.finished_at, JobRecord.updated_at)
        .cte("completed_job")
    )


async def requested_job_id(
    db: AsyncSession, user_id: int, request_id: str, fingerprint: str | None = None
) -> int | None:
    row = (
        await db.execute(
            select(JobRequest.job_id, JobRequest.fingerprint).where(
                JobRequest.user_id == user_id, JobRequest.request_id == request_id
            )
        )
    ).one_or_none()
    if row is None:
        return None
    if fingerprint is not None and row.fingerprint != fingerprint:
        raise ValueError("请求标识已用于不同的制作输入")
    return row.job_id


async def bind_job_request(db: AsyncSession, user_id: int, job_id: int, request_id: str, fingerprint: str) -> None:
    inserted = await db.scalar(
        insert(JobRequest)
        .values(user_id=user_id, job_id=job_id, request_id=request_id, fingerprint=fingerprint)
        .on_conflict_do_nothing(constraint="uq_job_requests_user_request")
        .returning(JobRequest.job_id)
    )
    if inserted is None:
        existing = await requested_job_id(db, user_id, request_id, fingerprint)
        if existing != job_id:
            raise ValueError("请求标识已经绑定另一个工作项")


async def clear_expired_job_checkpoints(db: AsyncSession, user_id: int) -> int:
    """仅压缩明确终态；未知提交、在途工作和引用关系不因保留期被删除。"""
    result = await db.execute(
        update(JobRecord)
        .where(
            JobRecord.user_id == user_id,
            JobRecord.state.in_(("succeeded", "failed", "cancelled")),
            JobRecord.cleanup_pending.is_(False),
            JobRecord.finished_at < utc_now() - timedelta(days=7),
            JobRecord.checkpoint.is_not(None),
        )
        .values(checkpoint=None)
    )
    return result.rowcount


async def bind_job_assets(db: AsyncSession, user_id: int, job_id: int, paths: Collection[str], role: str) -> None:
    assets = await ensure_assets_in_session(db, user_id, paths)
    await bind_job_asset_ids(db, user_id, job_id, list(assets.values()), role)


async def bind_job_asset_ids(
    db: AsyncSession, user_id: int, job_id: int, asset_ids: Collection[int], role: str
) -> None:
    if asset_ids:
        await db.execute(
            insert(JobAsset)
            .values(
                [
                    {"user_id": user_id, "job_id": job_id, "asset_id": asset_id, "role": role}
                    for asset_id in set(asset_ids)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_job_assets_role")
        )


async def job_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(
        await db.scalars(
            select(JobAsset.asset_id)
            .join(JobRecord, JobRecord.id == JobAsset.job_id)
            .where(
                JobAsset.user_id == user_id,
                or_(
                    JobRecord.cleanup_pending.is_(True),
                    JobRecord.finished_at.is_(None),
                    JobRecord.finished_at >= utc_now() - timedelta(days=7),
                ),
            )
        )
    )
    return set((await asset_paths_in_session(db, user_id, ids)).values())


async def cleanup_job_assets(db: AsyncSession, user_id: int) -> set[str]:
    completed = select(JobRecord.id).where(
        JobRecord.user_id == user_id,
        JobRecord.state.in_(("succeeded", "failed", "cancelled")),
        JobRecord.cleanup_pending.is_(False),
        JobRecord.finished_at < utc_now() - timedelta(days=7),
    )
    asset_ids = list(await db.scalars(select(JobAsset.asset_id).where(JobAsset.job_id.in_(completed))))
    paths = set((await asset_paths_in_session(db, user_id, asset_ids)).values())
    await db.execute(delete(JobAsset).where(JobAsset.job_id.in_(completed)))
    await clear_expired_job_checkpoints(db, user_id)
    await db.execute(
        update(JobRecord)
        .where(
            JobRecord.user_id == user_id,
            JobRecord.state.in_(("succeeded", "failed", "cancelled")),
            JobRecord.cleanup_pending.is_(False),
            JobRecord.finished_at < utc_now() - timedelta(days=90),
        )
        .values(error=None)
    )
    return paths


async def rewrite_job_checkpoints(
    db: AsyncSession,
    user_id: int,
    job_ids: Collection[int],
    rewrite: Callable[[dict | None], dict | None],
) -> None:
    if not job_ids:
        return
    rows = await db.execute(
        select(JobRecord.id, JobRecord.checkpoint).where(
            JobRecord.user_id == user_id,
            JobRecord.id.in_(job_ids),
        )
    )
    rewritten = [(job_id, rewrite(checkpoint)) for job_id, checkpoint in rows]
    for start in range(0, len(rewritten), 500):
        batch = (
            values(column("job_id", BigInteger), column("checkpoint", JSONB))
            .data(
                [
                    (job_id, checkpoint if checkpoint is not None else cast(null(), JSONB))
                    for job_id, checkpoint in rewritten[start : start + 500]
                ]
            )
            .alias("rewritten")
        )
        await db.execute(
            update(JobRecord.__table__)
            .where(JobRecord.id == batch.c.job_id, JobRecord.user_id == user_id)
            .values(checkpoint=batch.c.checkpoint)
        )


async def settled_job_ids(db: AsyncSession) -> set[int]:
    return set(
        await db.scalars(
            select(JobRecord.id).where(
                JobRecord.state.in_(("succeeded", "failed", "cancelled")), JobRecord.cleanup_pending.is_(False)
            )
        )
    )
