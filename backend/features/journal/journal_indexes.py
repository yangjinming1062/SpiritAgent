"""日记发布资料通过公共快照投影为可重建的记忆。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.entries.contracts import EntryView
from features.entries.queries import diary_texts
from features.memory.contracts import DiaryIndexSource
from features.memory.memory_narratives import index_diary_memories, index_diary_memory, remove_absent_diary_indexes


async def index_entry(db: AsyncSession, entry: EntryView) -> None:
    await index_diary_memory(db, DiaryIndexSource(entry.user_id, entry.entry_date, entry.title, entry.body))


async def rebuild_diary_indexes(db: AsyncSession, user_id: int) -> None:
    entries = [DiaryIndexSource(user_id, day, title, body) for day, title, body in await diary_texts(db, user_id)]
    await remove_absent_diary_indexes(db, user_id, [entry.entry_date for entry in entries])
    await index_diary_memories(db, entries)
