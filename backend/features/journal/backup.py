"""模块拥有的备份表、校验与恢复操作。"""

from functools import partial
from typing import Any

from infrastructure.backup import BackupContext, BackupRows, BackupTable
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.backup import clear_diary_indexes

from .journal_indexes import rebuild_diary_indexes
from .models import CompanionDiaryEntry
from .schemas import DiaryContent


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    id_map = context.id_map
    if table == "companion_diary_entries":
        payload["post_ids"] = [
            str(id_map["companion_posts"][str(value)])
            for value in raw.get("post_ids", [])
            if str(value) in id_map.get("companion_posts", {})
        ]


async def _check(db: AsyncSession, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    if not isinstance(payload.get("is_read"), bool):
        raise ValueError("Invalid diary read state")
    content = DiaryContent.model_validate({key: payload.get(key) for key in ("title", "body")})
    payload.update(content.model_dump())


async def _clear(db: AsyncSession, user_id: int) -> None:
    await clear_diary_indexes(db, user_id)
    await db.execute(delete(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id))


async def _restore_context(db: AsyncSession, rows: BackupRows, context: BackupContext) -> None:
    await rebuild_diary_indexes(db, context.user_id)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "companion_diary_entries",
            CompanionDiaryEntry,
            unique_keys=("entry_date",),
            prepare_payload=partial(_prepare, "companion_diary_entries"),
            validate_payload=_check,
            custom_delete=_clear,
            restore_context=_restore_context,
        ),
    )
