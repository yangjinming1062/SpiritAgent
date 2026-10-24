"""生活状态的备份贡献。"""

from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeEvent, LifePursuit, LifeState, LifeStep


async def _find_pursuit(
    db: AsyncSession,
    _raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key:
        return None
    return await db.scalar(
        select(LifePursuit.id).where(
            LifePursuit.user_id == context.user_id,
            LifePursuit.idempotency_key == key,
        ),
    )


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable("life_pursuits", LifePursuit, find_existing=_find_pursuit),
        BackupTable("life_steps", LifeStep, foreign_keys={"pursuit_id": "life_pursuits"}),
        BackupTable(
            "life_events",
            LifeEvent,
            unique_keys=("event_key",),
            foreign_keys={"pursuit_id": "life_pursuits", "step_id": "life_steps"},
        ),
        BackupTable("life_states", LifeState, unique_keys=(), foreign_keys={"active_pursuit_id": "life_pursuits"}),
    )
