"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupTable
from runtime.memory_scope import MemoryScope, validate_memory_scope
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.backup import validate_automation_conversation

from .models import CronJob


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    user_id = context.user_id
    if table == "cron_jobs":
        validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))
        if payload.get("kind") == "special" and payload["system_preset_id"] != "companion":
            raise ValueError("Special job belongs to a different preset")


async def _check(db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if payload.get("conversation_id") is not None:
        await validate_automation_conversation(db, payload["conversation_id"], context.user_id)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "cron_jobs",
            CronJob,
            foreign_keys={"conversation_id": "conversations"},
            prepare_payload=partial(_prepare, "cron_jobs"),
            validate_payload=_check,
        ),
    )
