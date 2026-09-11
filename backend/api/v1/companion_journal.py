"""伙伴日记 / 时刻 REST 端点。"""

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
    MomentCreateRequest,
    MomentListResponse,
    MomentResponse,
    MomentUpdateRequest,
)
from services.companion import (
    DiaryNotFoundError,
    MomentNotFoundError,
    create_user_diary,
    create_user_moment,
    get_diary_by_date,
    list_diary,
    list_moments,
    response_for_diary,
    response_for_moment,
    soft_delete_moment,
    update_diary,
    update_moment,
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


@router.post("/moments", response_model=MomentResponse, status_code=201)
async def post_moment(
    user: CurrentUser,
    db: DbSession,
    body: MomentCreateRequest,
) -> MomentResponse:
    media_url = (
        (
            body.media_id
            if body.media_id.startswith(("/", "http://", "https://"))
            else f"/api/media/files/{body.media_id}"
        )
        if body.media_id
        else None
    )
    row = await create_user_moment(
        db,
        user.id,
        title=body.title,
        body=body.body,
        emotion=body.emotion,
        media_url=media_url,
        kind=body.kind,
    )
    return MomentResponse(**response_for_moment(row))


@router.patch("/moments/{moment_id}", response_model=MomentResponse)
async def patch_moment(
    user: CurrentUser,
    db: DbSession,
    moment_id: str,
    body: MomentUpdateRequest,
) -> MomentResponse:
    try:
        row = await update_moment(
            db,
            user.id,
            moment_id,
            title=body.title,
            body=body.body,
            visibility=body.visibility,
        )
    except MomentNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": "moment not found", "reason": str(exc)})
    return MomentResponse(**response_for_moment(row))


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
