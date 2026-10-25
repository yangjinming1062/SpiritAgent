from datetime import date

from features.automation.schemas import NightlyActivityLogItem, NightlyActivityLogListResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.nightly.models import NightlyActivityLog


async def list_nightly_activity_logs(
    db: AsyncSession,
    user_id: int | None,
    target_date: date | None,
) -> NightlyActivityLogListResponse:
    """查询夜间自主活动日志。可选按 user_id 和 target_date 过滤。"""
    stmt = select(NightlyActivityLog).order_by(NightlyActivityLog.id.desc()).limit(300)
    if user_id is not None:
        stmt = stmt.where(NightlyActivityLog.user_id == user_id)
    if target_date is not None:
        stmt = stmt.where(NightlyActivityLog.target_date == target_date)
    rows = (await db.execute(stmt)).scalars().all()
    return NightlyActivityLogListResponse(items=[NightlyActivityLogItem.model_validate(row) for row in rows])
