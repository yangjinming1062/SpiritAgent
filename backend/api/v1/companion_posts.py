"""独立动态、评论与回复重试。"""

from uuid import UUID

from common import get_router
from components import DbSession
from fastapi import HTTPException, Query
from modules.auth import CurrentUser
from modules.companion import PostCommentCreateRequest, PostCommentResponse, PostListResponse, PostResponse
from services.application.posts import schedule_companion_reply
from services.domains.posts import (
    PostError,
    PostNotFoundError,
    create_comment,
    delete_comment,
    get_post,
    list_posts,
    response_for_comment,
    response_for_post,
    retry_reply,
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
    try:
        rows, next_cursor = await list_posts(db, user.id, cursor=cursor, limit=limit)
    except PostError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PostListResponse(posts=[response_for_post(row) for row in rows], next_cursor=next_cursor)


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
