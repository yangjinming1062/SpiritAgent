"""日记拥有发布资料，将只读索引输入交给记忆模块。"""

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.memory.contracts import DiaryIndexSource
from features.memory.memory_narratives import index_diary_memory

from .models import CompanionDiaryEntry


async def index_entry(db: AsyncSession, entry: CompanionDiaryEntry) -> None:
    await index_diary_memory(db, DiaryIndexSource(entry.user_id, entry.entry_date, entry.title, entry.body))


async def rebuild_diary_indexes(db: AsyncSession, user_id: int) -> None:
    for entry in await db.scalars(select(CompanionDiaryEntry).where(CompanionDiaryEntry.user_id == user_id)):
        await index_entry(db, entry)


async def delete_diary_record(db: AsyncSession, user_id: int, entry_date: date) -> str | None:
    return await db.scalar(
        delete(CompanionDiaryEntry)
        .where(CompanionDiaryEntry.user_id == user_id, CompanionDiaryEntry.entry_date == entry_date)
        .returning(CompanionDiaryEntry.id),
    )
