"""日记和反思的恢复结论，与对应业务写入共用短事务。"""

from typing import Literal

from modules.scheduler import NightlyActivityLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def load_narrative_result(db: AsyncSession, log_id: int, stage: Literal["journal", "reflection"]) -> bool | None:
    log = await db.get(NightlyActivityLog, log_id)
    results = (log.payload or {}).get("narrative_results", {}) if log is not None else {}
    result = results.get(stage) if isinstance(results, dict) else None
    return result if isinstance(result, bool) else None


async def save_narrative_result(
    db: AsyncSession,
    log_id: int,
    stage: Literal["journal", "reflection"],
    result: bool,
) -> None:
    log = await db.scalar(select(NightlyActivityLog).where(NightlyActivityLog.id == log_id).with_for_update())
    if log is None:
        raise ValueError("Nightly log no longer exists")
    payload = log.payload or {}
    log.payload = {**payload, "narrative_results": {**payload.get("narrative_results", {}), stage: result}}
