"""按已发布日记的正文建立索引。"""

from datetime import date

from infrastructure.database import SESSION_LOCAL
from infrastructure.search import SearchStatus, embed_search_query
from infrastructure.search_index import SearchDocument, search_documents, upsert_search_documents
from runtime.search_indexing import SearchIndexWorker
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DiaryEntry, DiarySearchIndex
from .queries import date_scoped_diaries

DIARY_SEARCH_WORKER = SearchIndexWorker("journal.diary_search", DiarySearchIndex)


def schedule_diary_embeddings(user_id: int) -> None:
    DIARY_SEARCH_WORKER.schedule(user_id)


def _diary_document(entry: DiaryEntry) -> SearchDocument:
    return SearchDocument(entry.id, f"{entry.title}\n\n{entry.body}")


async def index_diary(db: AsyncSession, entry: DiaryEntry) -> None:
    await upsert_search_documents(db, DiarySearchIndex, entry.user_id, [_diary_document(entry)])


async def rebuild_diary_search(db: AsyncSession, user_id: int) -> None:
    indexed = exists().where(DiarySearchIndex.user_id == user_id, DiarySearchIndex.record_id == DiaryEntry.id)
    while entries := (
        await db.scalars(
            select(DiaryEntry).where(DiaryEntry.user_id == user_id, ~indexed).order_by(DiaryEntry.id).limit(100)
        )
    ).all():
        await upsert_search_documents(db, DiarySearchIndex, user_id, [_diary_document(entry) for entry in entries])


async def search_diary_entries(
    user_id: int, *, query: str = "", date_from: date | None = None, date_to: date | None = None, limit: int = 20
) -> tuple[list[DiaryEntry], SearchStatus]:
    if not 1 <= limit <= 50 or len(query) > 1000:
        raise ValueError("检索数量须为 1–50，搜索词不超过 1000 字符")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    query = query.strip()
    statement = date_scoped_diaries(user_id, date_from, date_to)
    if query:
        vector = await embed_search_query(user_id, query)
        ids, status = await search_documents(
            DiarySearchIndex,
            user_id,
            statement.with_only_columns(DiaryEntry.id),
            query,
            vector=vector,
            limit=limit,
        )
        async with SESSION_LOCAL() as db:
            found = {row.id: row for row in await db.scalars(statement.where(DiaryEntry.id.in_(ids)))}
        rows = [found[identifier] for identifier in ids if identifier in found]
        DIARY_SEARCH_WORKER.schedule(user_id, vector)
    else:
        async with SESSION_LOCAL() as db:
            rows = list(await db.scalars(statement.order_by(DiaryEntry.entry_date.desc()).limit(limit)))
        status = SearchStatus(mode="timeline")
    return rows, status
