"""伙伴日记 REST 端点。"""

from datetime import date

from common import get_router
from components import DbSession
from fastapi import Query
from modules.auth import CurrentUser
from modules.companion import DiaryListResponse
from services.domains.journal import list_diary, response_for_diary

router = get_router(prefix="/api/companion", tag="companion")


@router.get("/diary", response_model=DiaryListResponse)
async def get_diary(
    user: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=365),
) -> DiaryListResponse:
    rows = await list_diary(db, user.id, date_from=date_from, date_to=date_to, limit=limit)
    return DiaryListResponse(entries=[response_for_diary(r) for r in rows])
