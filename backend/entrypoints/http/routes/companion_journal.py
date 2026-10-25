"""伙伴日记 REST 端点。"""

from datetime import date

from fastapi import Query
from features.journal.journal_service import has_unread_diary, list_diary, mark_diary_read, unread_diary_ids
from features.journal.schemas import DiaryListResponse, DiaryReadRequest, DiaryUnreadResponse
from infrastructure.database import DbSession

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion", tag="companion", remote=True)


@router.get("/diary", response_model=DiaryListResponse)
async def get_diary(
    user: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=365),
) -> DiaryListResponse:
    unread_ids = await unread_diary_ids(db, user.id)
    rows = await list_diary(db, user.id, date_from=date_from, date_to=date_to, limit=limit)
    return DiaryListResponse(entries=rows, unread_diary_ids=unread_ids)


@router.get("/diary/read-state", response_model=DiaryUnreadResponse)
async def get_diary_unread(user: CurrentUser, db: DbSession) -> DiaryUnreadResponse:
    return DiaryUnreadResponse(has_unread=await has_unread_diary(db, user.id))


@router.patch("/diary/read-state", response_model=DiaryUnreadResponse)
async def read_diary(user: CurrentUser, db: DbSession, body: DiaryReadRequest) -> DiaryUnreadResponse:
    return DiaryUnreadResponse(has_unread=await mark_diary_read(db, user.id, body.diary_ids))
