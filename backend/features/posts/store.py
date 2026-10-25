"""动态发布事务；内容、评论和附件通过 entries 领域操作。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from infrastructure.assets.asset_store import signed_companion_asset_url
from infrastructure.assets.paths import parse_companion_asset_path
from infrastructure.config import SETTINGS
from infrastructure.domain_locks import lock_domain
from infrastructure.text import ensure_utc, format_local_iso, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import user_is_active
from features.entries import queries as entries_queries
from features.entries import service as entries_service
from features.entries.contracts import CommentView, EntryError, EntryNotFoundError, EntryView, PostContentType
from features.posts.ports import notify_post_published
from features.posts.publication_models import PostPublication
from features.posts.schemas import PostCommentResponse, PostContext, PostPublicationResult, PostResponse


class PostError(RuntimeError):
    pass


class PostNotFoundError(PostError):
    pass


class PostBlockedError(PostError):
    """动态创作开关或发布额度阻止了本次发布。"""


@dataclass(frozen=True)
class PostInteractions:
    threads: list[dict]
    posted_ids: list[int]


def response_for_comment(row: CommentView) -> PostCommentResponse:
    return PostCommentResponse.model_validate(row, from_attributes=True)


def publication_status(row: PostPublication) -> str:
    return row.outcome or ("result_unknown" if row.state == "unknown" else row.state)


def response_for_publication(row: PostPublication) -> PostPublicationResult:
    return PostPublicationResult(
        publication_id=row.id, status=publication_status(row), post_id=row.post_id, error=row.error
    )


def response_for_post(row: EntryView) -> PostResponse:
    return PostResponse.model_validate(row, from_attributes=True).model_copy(
        update={
            "media_url": signed_companion_asset_url(row.media_url) if row.media_url else None,
            "audio_url": signed_companion_asset_url(row.audio_url) if row.audio_url else None,
        }
    )


async def get_post(db: AsyncSession, user_id: int, post_id: int) -> EntryView | None:
    return await entries_queries.get_entry(db, user_id, post_id, kind="post")


async def has_unread_posts(db: AsyncSession, user_id: int) -> bool:
    return await entries_queries.has_unread_entries(db, user_id, "post")


async def mark_posts_read(db: AsyncSession, user_id: int, post_ids: Sequence[int]) -> bool:
    unread = await entries_service.mark_entries_read(db, user_id, "post", post_ids)
    await db.commit()
    return unread


async def list_posts(
    db: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[EntryView], str | None, list[int]]:
    try:
        return await entries_queries.list_posts(db, user_id, cursor=cursor, limit=limit)
    except EntryError as exc:
        raise PostError(str(exc)) from exc


async def reserve_publication(db: AsyncSession, user_id: int, *, activity_date: date) -> PostPublication:
    await lock_domain(db, user_id, "entries")
    if not await user_is_active(db, user_id):
        raise PostError("账户不可用")
    if await publication_quota_remaining(db, user_id) <= 0:
        raise PostBlockedError("最近24小时的动态发布额度已用完")
    row = PostPublication(
        user_id=user_id, activity_date=activity_date, request_json={}, checkpoint={}, state="queued", stage="planning"
    )
    db.add(row)
    await db.flush()
    await db.commit()
    return row


async def publication_quota_remaining(
    db: AsyncSession, user_id: int, *, exclude_publication_id: int | None = None
) -> int:
    """已发布、在途及24小时内待核对的任务共同占用账户滚动额度。"""
    since = utc_now() - timedelta(hours=24)
    posted = await entries_queries.count_posts_since(db, user_id, since)
    reservations = (
        select(func.count())
        .select_from(PostPublication)
        .where(
            PostPublication.user_id == user_id,
            or_(
                PostPublication.state.in_(("queued", "running", "paused")),
                (PostPublication.state == "unknown") & (PostPublication.reserved_at >= since),
            ),
        )
    )
    if exclude_publication_id is not None:
        reservations = reservations.where(PostPublication.id != exclude_publication_id)
    reserved = await db.scalar(reservations)
    return max(0, SETTINGS.post_autonomous_per_day - posted - (reserved or 0))


async def commit_publication(
    db: AsyncSession,
    task_id: int,
    *,
    title: str,
    body: str,
    content_type: PostContentType,
    media_url: str | None,
    audio_url: str | None,
    context: PostContext,
    partial: bool,
) -> EntryView:
    task = await db.scalar(
        select(PostPublication)
        .where(PostPublication.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise PostNotFoundError("发布任务已不存在")
    if task.post_id is not None:
        row = await get_post(db, task.user_id, task.post_id)
        if row is None:
            raise PostError("发布记录缺少动态")
        await notify_post_published(db, row.user_id, row.id)
        await db.commit()
        return row
    if content_type != PostContentType.TEXT and not media_url:
        raise PostError("主媒体尚未完成，不能发布动态")
    if content_type == PostContentType.TEXT and (media_url or audio_url):
        raise PostError("文字动态不附带媒体")
    if audio_url and content_type not in (PostContentType.IMAGE, PostContentType.VIDEO):
        raise PostError("只有图片或视频动态可以添加旁白")
    for path in (media_url, audio_url):
        if path is not None and (not (parsed := parse_companion_asset_path(path)) or parsed[0] != task.user_id):
            raise PostError("动态只能引用本账户的正式媒体资产")
    row = await entries_service.create_post(
        db,
        task.user_id,
        activity_date=task.activity_date,
        title=title,
        body=body,
        content_type=content_type.value,
        media_url=media_url,
        audio_url=audio_url,
        context=context.model_dump(),
    )
    task.post_id = row.id
    task.state = "succeeded"
    task.outcome = "partial" if partial else "published"
    task.stage = "complete"
    await notify_post_published(db, row.user_id, row.id)
    emit_ws_event(
        db,
        user_id=row.user_id,
        event_type="companion.post.created",
        payload=response_for_post(row).model_dump(mode="json"),
    )
    await db.commit()
    return row


def entry_error(exc: EntryError) -> PostError:
    return PostNotFoundError(str(exc)) if isinstance(exc, EntryNotFoundError) else PostError(str(exc))


async def create_comment(db: AsyncSession, user_id: int, post_id: int, content: str) -> CommentView:
    try:
        result = await entries_service.create_user_comment(db, user_id, post_id, content)
        await db.commit()
        return result
    except EntryError as exc:
        raise entry_error(exc) from exc


async def delete_comment(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> None:
    try:
        await entries_service.delete_user_comment(db, user_id, post_id, comment_id)
        await db.commit()
    except EntryError as exc:
        raise entry_error(exc) from exc


async def retry_reply(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> CommentView:
    try:
        result = await entries_service.retry_comment_reply(db, user_id, post_id, comment_id)
        await db.commit()
        return result
    except EntryError as exc:
        raise entry_error(exc) from exc


async def collect_post_interactions(
    db: AsyncSession, user_id: int, *, local_date: date, user_timezone: str, utc_start: datetime, utc_end: datetime
) -> PostInteractions:
    rows = await entries_queries.interaction_entries(db, user_id, local_date, utc_start, utc_end)
    comments = await entries_queries.comments_for_entries(db, user_id, [row.id for row in rows], before=utc_end)
    threads = [
        {
            "post_id": m.id,
            "title": m.title,
            "body": m.body,
            "content_type": m.content_type,
            "published_at": format_local_iso(m.published_at, user_timezone),
            "activity_date": m.activity_date.isoformat(),
            "published_for_day": m.activity_date == local_date,
            "media_context": PostContext.model_validate(m.context_json).model_dump(exclude={"voice_id"}),
            "comments": [
                {
                    "id": c.id,
                    "reply_to_comment_id": c.reply_to_comment_id,
                    "role": c.role,
                    "content": c.content,
                    "created_at": format_local_iso(c.created_at, user_timezone),
                    "in_window": utc_start <= ensure_utc(c.created_at) < utc_end,
                }
                for c in comments.get(m.id, [])
            ],
        }
        for m in rows
    ]
    return PostInteractions(threads, [row.id for row in rows if row.activity_date == local_date])
