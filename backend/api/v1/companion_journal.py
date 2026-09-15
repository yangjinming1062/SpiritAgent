"""伙伴时刻 / 日记 REST 端点。"""

from datetime import date

from common import get_router
from components import DbSession, get_logger
from fastapi import HTTPException, Query
from modules.auth import CurrentUser
from modules.companion import (
    DiaryCreateRequest,
    DiaryEntryResponse,
    DiaryListResponse,
    DiaryUpdateRequest,
    MomentCommentCreateRequest,
    MomentCommentResponse,
    MomentListResponse,
    MomentResponse,
)
from services.application.moments import schedule_companion_reply
from services.domains.journal import (
    DiaryNotFoundError,
    MomentNotFoundError,
    create_moment_comment,
    create_user_diary,
    delete_moment_comment,
    get_diary_by_date,
    list_diary,
    list_moments,
    response_for_comment,
    response_for_diary,
    response_for_moment,
    soft_delete_moment,
    update_diary,
)

router = get_router(prefix="/api/companion", tag="companion")
logger = get_logger(__name__)


@router.get("/moments", response_model=MomentListResponse)
async def get_moments(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: int = 20,
    kind: str | None = None,
) -> MomentListResponse:
    rows, next_cursor = await list_moments(db, user.id, cursor=cursor, limit=limit, kind=kind)
    return MomentListResponse(
        moments=[MomentResponse(**response_for_moment(r)) for r in rows],
        next_cursor=next_cursor,
    )


@router.delete("/moments/{moment_id}", status_code=204)
async def delete_moment(
    user: CurrentUser,
    db: DbSession,
    moment_id: str,
) -> None:
    try:
        await soft_delete_moment(db, user.id, moment_id)
    except MomentNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "moment not found", "reason": str(exc)})


@router.post(
    "/moments/{moment_id}/comments",
    response_model=MomentCommentResponse,
    status_code=201,
)
async def post_moment_comment(
    user: CurrentUser,
    db: DbSession,
    moment_id: str,
    body: MomentCommentCreateRequest,
) -> MomentCommentResponse:
    try:
        row = await create_moment_comment(db, user.id, moment_id, content=body.content)
    except MomentNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "moment not found", "reason": str(exc)})
    # 精灵回复在请求返回后异步生成，经 companion.moment.comment 事件推送
    schedule_companion_reply(user.id, moment_id)
    return MomentCommentResponse(**response_for_comment(row))


@router.delete("/moments/{moment_id}/comments/{comment_id}", status_code=204)
async def delete_moment_comment_route(
    user: CurrentUser,
    db: DbSession,
    moment_id: str,
    comment_id: str,
) -> None:
    try:
        await delete_moment_comment(db, user.id, moment_id, comment_id)
    except MomentNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "comment not found", "reason": str(exc)})


@router.get("/diary", response_model=DiaryListResponse)
async def get_diary(
    user: CurrentUser,
    db: DbSession,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=100, ge=1, le=365),
) -> DiaryListResponse:
    rows = await list_diary(db, user.id, date_from=date_from, date_to=date_to, limit=limit)
    return DiaryListResponse(entries=[DiaryEntryResponse(**response_for_diary(r)) for r in rows])


@router.get("/diary/{entry_date}", response_model=DiaryEntryResponse)
async def get_diary_entry(
    user: CurrentUser,
    db: DbSession,
    entry_date: date,
) -> DiaryEntryResponse:
    row = await get_diary_by_date(db, user.id, entry_date)
    if row is None:
        raise HTTPException(status_code=404, detail="diary not found")
    return DiaryEntryResponse(**response_for_diary(row))


@router.post("/diary", response_model=DiaryEntryResponse, status_code=201)
async def post_diary(
    user: CurrentUser,
    db: DbSession,
    body: DiaryCreateRequest,
) -> DiaryEntryResponse:
    row = await create_user_diary(
        db,
        user.id,
        entry_date=body.entry_date,
        title=body.title,
        body=body.body,
        mood=body.mood,
    )
    return DiaryEntryResponse(**response_for_diary(row))


@router.patch("/diary/{diary_id}", response_model=DiaryEntryResponse)
async def patch_diary(
    user: CurrentUser,
    db: DbSession,
    diary_id: str,
    body: DiaryUpdateRequest,
) -> DiaryEntryResponse:
    try:
        row = await update_diary(
            db,
            user.id,
            diary_id,
            title=body.title,
            body=body.body,
            mood=body.mood,
        )
    except DiaryNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "diary not found", "reason": str(exc)})
    return DiaryEntryResponse(**response_for_diary(row))
