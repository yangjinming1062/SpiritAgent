"""posts 请求用例；拥有事务、响应装配与后台任务受理。"""

from infrastructure.api_ids import ResourceId
from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from features.entries.contracts import EntryError
from features.entries.queries import list_comments
from features.posts.publication import (
    discard_publication_video,
    list_publication_recoveries,
    publication_recovery,
)
from features.posts.replies import schedule_companion_reply
from features.posts.schemas import (
    PostCommentCreateRequest,
    PostCommentListResponse,
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
)


def _error(exc: PostError) -> RequestError:
    return RequestError(status_code=404 if isinstance(exc, PostNotFoundError) else 409, message=str(exc))


async def get_posts(user_id: int, db: AsyncSession, cursor: str | None = None, limit: int = 20) -> PostListResponse:
    # 加载期间发布的新动态由展示后的独立确认处理。
    try:
        rows, next_cursor, unread_ids = await list_posts(db, user_id, cursor=cursor, limit=limit)
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
        has_unread=await mark_posts_read(db, user_id, body.post_ids),
    )


async def get_publication_recoveries(
    user_id: int, limit: int = 50, cursor: str | None = None
) -> PostPublicationRecoveryList:
    try:
        return await list_publication_recoveries(user_id, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise RequestError(status_code=400, message="无效的任务分页位置") from exc


async def get_publication_recovery(user_id: int, publication_id: ResourceId) -> PostPublicationRecovery:
    try:
        return await publication_recovery(user_id, publication_id)
    except PostError as exc:
        raise _error(exc) from exc


async def query_publication_recovery(
    user_id: int, db: AsyncSession, publication_id: ResourceId
) -> PostPublicationRecovery:
    await db.commit()
    try:
        return await publication_recovery(user_id, publication_id, query=True)
    except PostError as exc:
        raise _error(exc) from exc


async def discard_publication_recovery(
    user_id: int, db: AsyncSession, publication_id: ResourceId
) -> PostPublicationResult:
    await db.commit()
    try:
        return await discard_publication_video(user_id, publication_id)
    except PostError as exc:
        raise _error(exc) from exc


async def get_post_detail(user_id: int, db: AsyncSession, post_id: ResourceId) -> PostResponse:
    row = await get_post(db, user_id, post_id)
    if row is None:
        raise RequestError(status_code=404, message="找不到动态")
    comments, next_cursor = await list_comments(db, user_id, post_id)
    return response_for_post(row).model_copy(
        update={
            "comments": [response_for_comment(item) for item in comments],
            "comments_next_cursor": next_cursor,
            "comments_loaded": True,
        }
    )


async def get_comments(
    user_id: int, db: AsyncSession, post_id: ResourceId, *, cursor: str | None = None, limit: int = 50
) -> PostCommentListResponse:
    try:
        rows, next_cursor = await list_comments(db, user_id, post_id, cursor=cursor, limit=limit)
    except EntryError as exc:
        raise RequestError(status_code=400, message=str(exc)) from exc
    return PostCommentListResponse(comments=[response_for_comment(row) for row in rows], next_cursor=next_cursor)


async def post_comment(
    user_id: int,
    db: AsyncSession,
    post_id: ResourceId,
    body: PostCommentCreateRequest,
) -> PostCommentResponse:
    try:
        row = await create_comment(db, user_id, post_id, body.content)
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user_id, post_id)
    return response_for_comment(row)


async def remove_comment(user_id: int, db: AsyncSession, post_id: ResourceId, comment_id: ResourceId) -> None:
    try:
        await delete_comment(db, user_id, post_id, comment_id)
    except PostError as exc:
        raise _error(exc) from exc


async def retry_comment_reply(
    user_id: int, db: AsyncSession, post_id: ResourceId, comment_id: ResourceId
) -> PostCommentResponse:
    try:
        row = await retry_reply(db, user_id, post_id, comment_id)
    except PostError as exc:
        raise _error(exc) from exc
    schedule_companion_reply(user_id, post_id)
    return response_for_comment(row)
