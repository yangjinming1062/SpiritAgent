"""独立动态、评论与回复重试。"""

from uuid import UUID

from fastapi import HTTPException, Query
from features.posts import use_cases as _posts
from features.posts.schemas import (
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
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router(prefix="/api/companion/posts", tag="companion")


@router.get("", response_model=PostListResponse)
async def get_posts(
    user: CurrentUser,
    db: DbSession,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> PostListResponse:
    try:
        return await _posts.get_posts(user_id=user.id, db=db, cursor=cursor, limit=limit)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/unread", response_model=PostUnreadResponse)
async def get_posts_unread(user: CurrentUser, db: DbSession) -> PostUnreadResponse:
    try:
        return await _posts.get_posts_unread(user_id=user.id, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/read", response_model=PostUnreadResponse)
async def read_posts(user: CurrentUser, db: DbSession, body: PostReadRequest) -> PostUnreadResponse:
    try:
        return await _posts.read_posts(user_id=user.id, db=db, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/publications/recovery", response_model=PostPublicationRecoveryList)
async def get_publication_recoveries(
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PostPublicationRecoveryList:
    try:
        return await _posts.get_publication_recoveries(user_id=user.id, limit=limit, offset=offset)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/publications/{publication_id}", response_model=PostPublicationRecovery)
async def get_publication_recovery(user: CurrentUser, publication_id: UUID) -> PostPublicationRecovery:
    try:
        return await _posts.get_publication_recovery(user_id=user.id, publication_id=publication_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/publications/{publication_id}/query", response_model=PostPublicationRecovery)
async def query_publication_recovery(user: CurrentUser, db: DbSession, publication_id: UUID) -> PostPublicationRecovery:
    try:
        return await _posts.query_publication_recovery(user_id=user.id, db=db, publication_id=publication_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/publications/{publication_id}/adopt", response_model=PostPublicationResult)
async def adopt_publication_recovery(user: CurrentUser, publication_id: UUID) -> PostPublicationResult:
    try:
        return await _posts.adopt_publication_recovery(user_id=user.id, publication_id=publication_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/publications/{publication_id}/discard", response_model=PostPublicationResult)
async def discard_publication_recovery(user: CurrentUser, db: DbSession, publication_id: UUID) -> PostPublicationResult:
    try:
        return await _posts.discard_publication_recovery(user_id=user.id, db=db, publication_id=publication_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{post_id}", response_model=PostResponse)
async def get_post_detail(user: CurrentUser, db: DbSession, post_id: UUID) -> PostResponse:
    try:
        return await _posts.get_post_detail(user_id=user.id, db=db, post_id=post_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{post_id}/comments", response_model=PostCommentResponse, status_code=201)
async def post_comment(
    user: CurrentUser,
    db: DbSession,
    post_id: UUID,
    body: PostCommentCreateRequest,
) -> PostCommentResponse:
    try:
        return await _posts.post_comment(user_id=user.id, db=db, post_id=post_id, body=body)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/{post_id}/comments/{comment_id}", status_code=204)
async def remove_comment(user: CurrentUser, db: DbSession, post_id: UUID, comment_id: UUID) -> None:
    try:
        return await _posts.remove_comment(user_id=user.id, db=db, post_id=post_id, comment_id=comment_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{post_id}/comments/{comment_id}/reply/retry", response_model=PostCommentResponse)
async def retry_comment_reply(user: CurrentUser, db: DbSession, post_id: UUID, comment_id: UUID) -> PostCommentResponse:
    try:
        return await _posts.retry_comment_reply(user_id=user.id, db=db, post_id=post_id, comment_id=comment_id)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
