"""夜间账本的恢复候选与过期收口；执行状态归关联生活步骤。"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import NightlyActivityLog

NIGHTLY_UNFINISHED_STATUSES: tuple[str, ...] = ("running", "failed")
_COMPLETED_STATUSES = ("completed", "completed_with_errors")


@dataclass(frozen=True)
class NightlyLog:
    id: int
    user_id: int
    target_date: date
    status: str

    @property
    def is_completed(self) -> bool:
        return self.status in _COMPLETED_STATUSES


async def nightly_logs(db: AsyncSession, user_ids: Collection[int], *, since: date) -> dict[int, list[NightlyLog]]:
    """读取恢复窗口内的账本，以及任何仍未收口的旧账本。"""
    if not user_ids:
        return {}
    rows = await db.scalars(
        select(NightlyActivityLog)
        .where(
            NightlyActivityLog.user_id.in_(user_ids),
            or_(NightlyActivityLog.target_date >= since, NightlyActivityLog.status.in_(NIGHTLY_UNFINISHED_STATUSES)),
        )
        .order_by(NightlyActivityLog.target_date.desc())
    )
    result: dict[int, list[NightlyLog]] = {}
    for row in rows:
        result.setdefault(row.user_id, []).append(NightlyLog(row.id, row.user_id, row.target_date, row.status))
    return result


async def close_expired_logs(db: AsyncSession, log_ids: Collection[int]) -> None:
    if log_ids:
        await db.execute(
            update(NightlyActivityLog)
            .where(
                NightlyActivityLog.id.in_(log_ids),
                NightlyActivityLog.status.in_(NIGHTLY_UNFINISHED_STATUSES),
            )
            .values(status="completed_with_errors", summary="已超过恢复窗口，未确认动作不再重放")
        )
