"""夜间账本向调度流程提供冻结恢复候选与过期收口操作。"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import NightlyActivityLog


@dataclass(frozen=True)
class UnfinishedNightlyLog:
    id: int
    user_id: int
    target_date: date


async def unfinished_logs(db: AsyncSession, user_ids: Collection[int]) -> dict[int, list[UnfinishedNightlyLog]]:
    result: dict[int, list[UnfinishedNightlyLog]] = {}
    rows = await db.scalars(
        select(NightlyActivityLog)
        .where(NightlyActivityLog.user_id.in_(user_ids), NightlyActivityLog.status.in_(("running", "failed")))
        .order_by(NightlyActivityLog.target_date.desc()),
    )
    for row in rows:
        result.setdefault(row.user_id, []).append(UnfinishedNightlyLog(row.id, row.user_id, row.target_date))
    return result


async def close_expired_logs(db: AsyncSession, log_ids: Collection[int]) -> None:
    if log_ids:
        await db.execute(
            update(NightlyActivityLog)
            .where(NightlyActivityLog.id.in_(log_ids))
            .values(status="completed_with_errors", summary="已超过恢复窗口，未确认动作不再重放"),
        )
