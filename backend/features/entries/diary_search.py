"""日记索引归内容领域，正文只从已发布日记读取。"""

from datetime import date

from infrastructure.database import session_scope
from infrastructure.search import SearchStatus, embed_search_query
from infrastructure.search_index import SearchDocument, search_documents, upsert_search_documents
from runtime.search_indexing import SearchIndexWorker
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import EntryView
from .models import CompanionEntry, DiarySearchIndex
from .queries import entry_query, read_entries

DIARY_SEARCH_WORKER = SearchIndexWorker("entries.diary_search", DiarySearchIndex)


def schedule_diary_embeddings(user_id: int) -> None:
    DIARY_SEARCH_WORKER.schedule(user_id)


async def index_diary(db: AsyncSession, entry: EntryView) -> None:
    if entry.kind != "diary":
        raise ValueError("Only published diaries can be indexed")
    await upsert_search_documents(
        db, DiarySearchIndex, entry.user_id, [SearchDocument(entry.id, f"{entry.title}\n\n{entry.body}")]
    )


async def rebuild_diary_search(db: AsyncSession, user_id: int) -> None:
    indexed = exists().where(DiarySearchIndex.user_id == user_id, DiarySearchIndex.record_id == CompanionEntry.id)
    while True:
        entries = await read_entries(
            db,
            user_id,
            entry_query().where(CompanionEntry.kind == "diary", ~indexed).order_by(CompanionEntry.id).limit(100),
        )
        if not entries:
            return
        await upsert_search_documents(
            db,
            DiarySearchIndex,
            user_id,
            [SearchDocument(entry.id, f"{entry.title}\n\n{entry.body}") for entry in entries],
        )


async def search_diary_entries(
    user_id: int, *, query: str = "", date_from: date | None = None, date_to: date | None = None, limit: int = 20
) -> tuple[list[EntryView], SearchStatus]:
    if not 1 <= limit <= 50 or len(query) > 1000:
        raise ValueError("检索数量须为 1–50，搜索词不超过 1000 字符")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期")
    query = query.strip()
    vector = await embed_search_query(user_id, query)
    async with session_scope() as db:
        where = [CompanionEntry.user_id == user_id, CompanionEntry.kind == "diary"]
        if date_from is not None:
            where.append(CompanionEntry.activity_date >= date_from)
        if date_to is not None:
            where.append(CompanionEntry.activity_date <= date_to)
        if query:
            ids, status = await search_documents(
                db,
                DiarySearchIndex,
                user_id,
                select(CompanionEntry.id).where(*where),
                query,
                vector=vector,
                limit=limit,
            )
            found = {
                row.id: row
                for row in await read_entries(db, user_id, entry_query().where(*where, CompanionEntry.id.in_(ids)))
            }
            rows = [found[identifier] for identifier in ids if identifier in found]
        else:
            rows = await read_entries(
                db,
                user_id,
                entry_query()
                .where(*where)
                .order_by(CompanionEntry.activity_date.desc(), CompanionEntry.id.desc())
                .limit(limit),
            )
            status = SearchStatus(mode="timeline")
    if query:
        DIARY_SEARCH_WORKER.schedule(user_id, vector)
    return rows, status
