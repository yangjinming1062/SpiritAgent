"""独立动态、评论与回复重试。"""

from uuid import UUID

from common import get_router
from components import DbSession
from fastapi import HTTPException, Query
from modules.auth import CurrentUser
from modules.companion import (
    PostCommentCreateRequest,
    PostCommentResponse,
    PostListResponse,
    PostPublicationRecovery,
    PostPublicationRecoveryList,
    PostPublicationResult,
    PostReadRequest,
    PostResponse,
    PostUnreadResponse,
)
from services.application.posts import (
    adopt_publication_video,
    discard_publication_video,
    list_publication_recoveries,
    publication_recovery,
    schedule_companion_reply,
)
from services.domains.posts import (
    PostError,
    PostNotFoundError,
    create_comment,
    delete_comment,
    get_post,
    has_unread_posts,
    list_posts,
    mark_posts_read,
    response_for_comment,
    response_for_post,
    retry_reply,
    unread_post_ids,
)

router = get_router(prefix="/api/companion/posts", tag="companion")


def _error(exc: PostError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, PostNotFoundError) else 409, detail=str(exc))


@router.get("", response_model=PostListResponse)
async def get_posts(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> PostListResponse:
    # 先捕获快照，加载期间新发布的动态由展示后的独立确认处理。
    unread_ids = await unread_post_ids(db, user.id) if cursor is None else []
    try:
        rows, next_cursor = await list_posts(db, user.id, cursor=cursor, limit=limit)
    except PostError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PostListResponse(
        posts=[response_for_post(row) for row in rows],
        next_cursor=next_cursor,
        unread_post_ids=unread_ids,
    )


@router.get("/unread", response_model=PostUnreadResponse)
async def get_posts_unread(user: CurrentUser, db: DbSession) -> PostUnreadResponse:
    return PostUnreadResponse(has_unread=await has_unread_posts(db, user.id))


@router.post("/read", response_model=PostUnreadResponse)
async def read_posts(user: CurrentUser, db: DbSession, body: PostReadRequest) -> PostUnreadResponse:
    return PostUnreadResponse(
        has_unread=await mark_posts_read(db, user.id, [str(post_id) for post_id in body.post_ids]),
    )


@router.get("/publications/recovery", response_model=PostPublicationRecoveryList)
async def get_publication_recoveries(
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PostPublicationRecoveryList:
    return await list_publication_recoveries(user.id, limit=limit, offset=offset)


@router.get("/publications/{publication_id}", response_model=PostPublicationRecovery)
async def get_publication_recovery(user: CurrentUser, publication_id: UUID) -> PostPublicationRecovery:
    try:
        return await publication_recovery(user.id, str(publication_id))
    except PostError as exc:
        raise _error(exc) from exc


@router.post("/publications/{publication_id}/query", response_model=PostPublicationRecovery)
async def query_publication_recovery(user: CurrentUser, db: DbSession, publication_id: UUID) -> PostPublicationRecovery:
    await db.commit()
    try:
        return await publication_recovery(user.id, str(publication_id), query=True)
    except PostError as exc:
        raise _error(exc) from exc


@router.post("/publications/{publication_id}/adopt", response_model=PostPublicationResult)
async def adopt_publication_recovery(user: CurrentUser, publication_id: UUID) -> PostPublicationResult:
    try:
        return await adopt_publication_video(user.id, str(publication_id))
    except PostError as exc:
        raise _error(exc) from exc


@router.post("/publications/{publication_id}/discard", response_model=PostPublicationResult)
async def discard_publication_recovery(user: CurrentUser, db: DbSession, publication_id: UUID) -> PostPublicationResult:
    await db.commit()
    try:
        return await discard_publication_video(user.id, str(publication_id))
    except PostError as exc:
        raise _error(exc) from exc


@router.get("/{post_id}", response_model=PostResponse)
async def get_post_detail(user: CurrentUser, db: DbSession, post_id: UUID) -> PostResponse:
    row = await get_post(db, user.id, str(post_id))
    if row is None:
        raise HTTPException(status_code=404, detail="找不到动态")
    return response_for_post(row)


@router.post("/{post_id}/comments", response_model=PostCommentResponse, status_code=201)
async def post_comment(
    user: CurrentUser,
    db: DbSession,
    post_id: UUID,
    body: PostCommentCreateRequest,
) -> PostCommentResponse:
    try:
        row = await create_comment(db, user.id, str(post_id), body.content)
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user.id, str(post_id))
    return response_for_comment(row)


@router.delete("/{post_id}/comments/{comment_id}", status_code=204)
async def remove_comment(user: CurrentUser, db: DbSession, post_id: UUID, comment_id: UUID) -> None:
    try:
        await delete_comment(db, user.id, str(post_id), str(comment_id))
    except PostError as exc:
        raise _error(exc) from exc


@router.post("/{post_id}/comments/{comment_id}/reply/retry", response_model=PostCommentResponse)
async def retry_comment_reply(user: CurrentUser, db: DbSession, post_id: UUID, comment_id: UUID) -> PostCommentResponse:
    try:
        row = await retry_reply(db, user.id, str(post_id), str(comment_id))
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user.id, str(post_id))
    return response_for_comment(row)
