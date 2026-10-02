"""伙伴日记 REST 端点。"""

from datetime import date

from common import get_router
from components import DbSession
from fastapi import Query
from modules.auth import CurrentUser
from modules.companion import DiaryListResponse, DiaryReadRequest, DiaryUnreadResponse
from services.domains.journal import has_unread_diary, list_diary, mark_diary_read, response_for_diary, unread_diary_ids

router = get_router(prefix="/api/companion", tag="companion")


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
    return DiaryListResponse(entries=[response_for_diary(r) for r in rows], unread_diary_ids=unread_ids)


@router.get("/diary/unread", response_model=DiaryUnreadResponse)
async def get_diary_unread(user: CurrentUser, db: DbSession) -> DiaryUnreadResponse:
    return DiaryUnreadResponse(has_unread=await has_unread_diary(db, user.id))


@router.post("/diary/read", response_model=DiaryUnreadResponse)
async def read_diary(user: CurrentUser, db: DbSession, body: DiaryReadRequest) -> DiaryUnreadResponse:
    return DiaryUnreadResponse(has_unread=await mark_diary_read(db, user.id, [str(value) for value in body.diary_ids]))
