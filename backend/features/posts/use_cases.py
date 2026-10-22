"""posts 请求用例；拥有事务、响应装配与后台任务受理。"""

from uuid import UUID

from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from features.posts.publication import (
    discard_publication_video,
    list_publication_recoveries,
    publication_recovery,
)
from features.posts.replies import schedule_companion_reply
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
from features.posts.store import (
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


def _error(exc: PostError) -> RequestError:
    return RequestError(status_code=404 if isinstance(exc, PostNotFoundError) else 409, message=str(exc))


async def get_posts(user_id: int, db: AsyncSession, cursor: str | None = None, limit: int = 20) -> PostListResponse:
    # 加载期间发布的新动态由展示后的独立确认处理。
    unread_ids = await unread_post_ids(db, user_id) if cursor is None else []
    try:
        rows, next_cursor = await list_posts(db, user_id, cursor=cursor, limit=limit)
    except PostError as exc:
        raise RequestError(status_code=400, message=str(exc)) from exc
    return PostListResponse(
        posts=[response_for_post(row) for row in rows],
        next_cursor=next_cursor,
        unread_post_ids=unread_ids,
    )


async def get_posts_unread(user_id: int, db: AsyncSession) -> PostUnreadResponse:
    return PostUnreadResponse(has_unread=await has_unread_posts(db, user_id))


async def read_posts(user_id: int, db: AsyncSession, body: PostReadRequest) -> PostUnreadResponse:
    return PostUnreadResponse(
        has_unread=await mark_posts_read(db, user_id, [str(post_id) for post_id in body.post_ids]),
    )


async def get_publication_recoveries(user_id: int, limit: int = 50, offset: int = 0) -> PostPublicationRecoveryList:
    return await list_publication_recoveries(user_id, limit=limit, offset=offset)


async def get_publication_recovery(user_id: int, publication_id: UUID) -> PostPublicationRecovery:
    try:
        return await publication_recovery(user_id, str(publication_id))
    except PostError as exc:
        raise _error(exc) from exc


async def query_publication_recovery(user_id: int, db: AsyncSession, publication_id: UUID) -> PostPublicationRecovery:
    await db.commit()
    try:
        return await publication_recovery(user_id, str(publication_id), query=True)
    except PostError as exc:
        raise _error(exc) from exc


async def discard_publication_recovery(user_id: int, db: AsyncSession, publication_id: UUID) -> PostPublicationResult:
    await db.commit()
    try:
        return await discard_publication_video(user_id, str(publication_id))
    except PostError as exc:
        raise _error(exc) from exc


async def get_post_detail(user_id: int, db: AsyncSession, post_id: UUID) -> PostResponse:
    row = await get_post(db, user_id, str(post_id))
    if row is None:
        raise RequestError(status_code=404, message="找不到动态")
    return response_for_post(row)


async def post_comment(
    user_id: int,
    db: AsyncSession,
    post_id: UUID,
    body: PostCommentCreateRequest,
) -> PostCommentResponse:
    try:
        row = await create_comment(db, user_id, str(post_id), body.content)
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user_id, str(post_id))
    return response_for_comment(row)


async def remove_comment(user_id: int, db: AsyncSession, post_id: UUID, comment_id: UUID) -> None:
    try:
        await delete_comment(db, user_id, str(post_id), str(comment_id))
    except PostError as exc:
        raise _error(exc) from exc


async def retry_comment_reply(user_id: int, db: AsyncSession, post_id: UUID, comment_id: UUID) -> PostCommentResponse:
    try:
        row = await retry_reply(db, user_id, str(post_id), str(comment_id))
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user_id, str(post_id))
    return response_for_comment(row)
