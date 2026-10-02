"""动态持久化、额度预留、评论状态与夜间资料。"""

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from components import SETTINGS, ensure_utc, format_local_iso, utc_now
from modules.auth import User
from modules.companion import (
    CompanionPost,
    CompanionPostComment,
    PostCommentResponse,
    PostContentType,
    PostContext,
    PostPublication,
    PostPublicationResult,
    PostResponse,
)
from modules.ws import emit_ws_event
from pydantic import TypeAdapter
from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from services.infrastructure.assets import parse_companion_asset_path, signed_companion_asset_url

_CURSOR = TypeAdapter(tuple[datetime, UUID])


class PostError(RuntimeError):
    pass


class PostNotFoundError(PostError):
    pass


class PostBlockedError(PostError):
    """受理被自主发布开关、请求类型或发布额度拦截；与参数和系统错误区分，夜间账本据此记为阻止。"""


@dataclass(frozen=True)
class PostInteractions:
    threads: list[dict]
    posted_ids: list[str]


def response_for_comment(row: CompanionPostComment) -> PostCommentResponse:
    return PostCommentResponse.model_validate(row, from_attributes=True)


def response_for_publication(row: PostPublication) -> PostPublicationResult:
    return PostPublicationResult(publication_id=row.id, status=row.status, post_id=row.post_id, error=row.error)


def response_for_post(row: CompanionPost) -> PostResponse:
    return PostResponse.model_validate(row, from_attributes=True).model_copy(
        update={
            "media_url": signed_companion_asset_url(row.media_url) if row.media_url else None,
            "audio_url": signed_companion_asset_url(row.audio_url) if row.audio_url else None,
        },
    )


async def emit_comment(db: AsyncSession, row: CompanionPostComment) -> None:
    await db.flush()
    await db.refresh(row)
    emit_ws_event(
        db,
        user_id=row.user_id,
        event_type="companion.post.comment",
        payload={"post_id": row.post_id, "comment": response_for_comment(row).model_dump()},
    )


async def get_post(db: AsyncSession, user_id: int, post_id: str) -> CompanionPost | None:
    return await db.scalar(
        select(CompanionPost).where(
            CompanionPost.id == post_id,
            CompanionPost.user_id == user_id,
        ),
    )


async def unread_post_ids(db: AsyncSession, user_id: int) -> list[str]:
    return list(
        (
            await db.scalars(
                select(CompanionPost.id).where(CompanionPost.user_id == user_id, CompanionPost.is_read.is_(False)),
            )
        ).all(),
    )


async def has_unread_posts(db: AsyncSession, user_id: int) -> bool:
    return bool(
        await db.scalar(
            select(
                select(CompanionPost.id)
                .where(CompanionPost.user_id == user_id, CompanionPost.is_read.is_(False))
                .exists(),
            ),
        ),
    )


async def mark_posts_read(db: AsyncSession, user_id: int, post_ids: Sequence[str]) -> bool:
    await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    result = await db.execute(
        update(CompanionPost)
        .where(
            CompanionPost.user_id == user_id,
            CompanionPost.id.in_(post_ids),
            CompanionPost.is_read.is_(False),
        )
        .values(is_read=True),
    )
    if result.rowcount:
        emit_ws_event(db, user_id=user_id, event_type="companion.posts.read", payload={})
    has_unread = await has_unread_posts(db, user_id)
    await db.commit()
    return has_unread


async def list_posts(
    db: AsyncSession,
    user_id: int,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[CompanionPost], str | None]:
    stmt = select(CompanionPost).where(CompanionPost.user_id == user_id)
    if cursor:
        try:
            stamp, row_id = _CURSOR.validate_json(base64.urlsafe_b64decode(cursor), strict=True)
        except (ValueError, TypeError) as exc:
            raise PostError("无效的动态分页位置") from exc
        stmt = stmt.where(tuple_(CompanionPost.published_at, CompanionPost.id) < (ensure_utc(stamp), str(row_id)))
    rows = list(
        (
            await db.scalars(
                stmt.order_by(
                    CompanionPost.published_at.desc(),
                    CompanionPost.id.desc(),
                ).limit(limit + 1),
            )
        ).all(),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(
            _CURSOR.dump_json((rows[-1].published_at, UUID(rows[-1].id))),
        ).decode()
        if more
        else None
    )
    return rows, next_cursor


async def reserve_publication(
    db: AsyncSession,
    user_id: int,
    *,
    key: str,
    trigger: str,
    quota_kind: str,
    activity_date: date,
    request: dict,
) -> PostPublication:
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active:
        raise PostError("账户不可用")
    existing = await db.scalar(
        select(PostPublication).where(
            PostPublication.user_id == user_id,
            PostPublication.idempotency_key == key,
        ),
    )
    if existing is not None:
        return existing
    if await publication_quota_remaining(db, user_id, quota_kind) <= 0:
        raise PostBlockedError("最近24小时的动态发布额度已用完")
    row = PostPublication(
        user_id=user_id,
        idempotency_key=key,
        trigger=trigger,
        quota_kind=quota_kind,
        activity_date=activity_date,
        request_json=request,
        progress_json={},
        status="queued",
        phase="planning",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def publication_quota_remaining(db: AsyncSession, user_id: int, quota_kind: str) -> int:
    """最近24小时内该类别还可预留的发布数；已发布、排队或运行中的任务，以及24小时内结果未知的任务都占用额度。"""
    since = utc_now() - timedelta(hours=24)
    posted = await db.scalar(
        select(func.count())
        .select_from(CompanionPost)
        .where(
            CompanionPost.user_id == user_id,
            CompanionPost.quota_kind == quota_kind,
            CompanionPost.published_at >= since,
        ),
    )
    reserved = await db.scalar(
        select(func.count())
        .select_from(PostPublication)
        .where(
            PostPublication.user_id == user_id,
            PostPublication.quota_kind == quota_kind,
            or_(
                PostPublication.status.in_(("queued", "running")),
                (PostPublication.status == "result_unknown") & (PostPublication.reserved_at >= since),
            ),
        ),
    )
    limit = SETTINGS.post_autonomous_per_day if quota_kind == "autonomous" else SETTINGS.post_requested_per_day
    return max(0, limit - (posted or 0) - (reserved or 0))


async def publication_status(db: AsyncSession, user_id: int, task_id: str) -> PostPublicationResult:
    row = await db.scalar(
        select(PostPublication).where(
            PostPublication.id == task_id,
            PostPublication.user_id == user_id,
        ),
    )
    if row is None:
        raise PostNotFoundError("找不到发布任务")
    return response_for_publication(row)


async def commit_publication(
    db: AsyncSession,
    task_id: str,
    *,
    title: str,
    body: str,
    content_type: PostContentType,
    media_url: str | None,
    audio_url: str | None,
    context: PostContext,
    partial: bool,
) -> CompanionPost:
    initial = await db.get(PostPublication, task_id)
    if initial is None:
        raise PostNotFoundError("发布任务已不存在")
    await db.scalar(select(User).where(User.id == initial.user_id).with_for_update())
    task = await db.scalar(
        select(PostPublication)
        .where(PostPublication.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    )
    if task is None:
        raise PostNotFoundError("发布任务已不存在")
    if task.post_id:
        row = await get_post(db, task.user_id, task.post_id)
        if row is None:
            raise PostError("发布记录缺少动态")
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
    row = CompanionPost(
        user_id=task.user_id,
        published_at=utc_now(),
        activity_date=task.activity_date,
        quota_kind=task.quota_kind,
        title=title,
        body=body,
        content_type=content_type.value,
        media_url=media_url,
        audio_url=audio_url,
        context_json=context.model_dump(),
    )
    db.add(row)
    await db.flush()
    set_committed_value(row, "comments", [])
    task.post_id = row.id
    task.status = "partial" if partial else "published"
    task.phase = "complete"
    emit_ws_event(
        db,
        user_id=row.user_id,
        event_type="companion.post.created",
        payload=response_for_post(row).model_dump(),
    )
    await db.commit()
    return row


async def create_comment(db: AsyncSession, user_id: int, post_id: str, content: str) -> CompanionPostComment:
    content = content.strip()
    if not content or len(content) > 500:
        raise PostError("评论需要1至500字符")
    if (
        await db.scalar(select(CompanionPost.id).where(CompanionPost.id == post_id, CompanionPost.user_id == user_id))
        is None
    ):
        raise PostNotFoundError("找不到动态")
    row = CompanionPostComment(
        user_id=user_id,
        post_id=post_id,
        role="user",
        content=content,
        reply_status="pending",
        reply_attempts=0,
    )
    db.add(row)
    await emit_comment(db, row)
    await db.commit()
    return row


async def delete_comment(db: AsyncSession, user_id: int, post_id: str, comment_id: str) -> None:
    row = await db.scalar(
        select(CompanionPostComment)
        .where(
            CompanionPostComment.id == comment_id,
            CompanionPostComment.post_id == post_id,
            CompanionPostComment.user_id == user_id,
            CompanionPostComment.role == "user",
        )
        .with_for_update(),
    )
    if row is None:
        raise PostNotFoundError("找不到评论")
    await db.delete(row)
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.post.comment.deleted",
        payload={"post_id": post_id, "comment_id": comment_id},
    )
    await db.commit()


async def retry_reply(db: AsyncSession, user_id: int, post_id: str, comment_id: str) -> CompanionPostComment:
    row = await db.scalar(
        select(CompanionPostComment)
        .where(
            CompanionPostComment.id == comment_id,
            CompanionPostComment.post_id == post_id,
            CompanionPostComment.user_id == user_id,
            CompanionPostComment.role == "user",
        )
        .with_for_update(),
    )
    if row is None:
        raise PostNotFoundError("找不到评论")
    if row.reply_status != "failed":
        raise PostError("只有失败的回复可以重试")
    row.reply_status, row.reply_error = "pending", None
    await emit_comment(db, row)
    await db.commit()
    return row


async def collect_post_interactions(
    db: AsyncSession,
    user_id: int,
    *,
    local_date: date,
    user_timezone: str,
    utc_start: datetime,
    utc_end: datetime,
) -> PostInteractions:
    rows = list(
        (
            await db.scalars(
                select(CompanionPost)
                .where(
                    CompanionPost.user_id == user_id,
                    or_(
                        CompanionPost.activity_date == local_date,
                        CompanionPost.comments.any(
                            (CompanionPostComment.user_id == user_id)
                            & (CompanionPostComment.created_at >= utc_start)
                            & (CompanionPostComment.created_at < utc_end),
                        ),
                    ),
                )
                .order_by(CompanionPost.published_at, CompanionPost.id),
            )
        ).all(),
    )
    posted_ids = [row.id for row in rows if row.activity_date == local_date]
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
                for c in m.comments
                if ensure_utc(c.created_at) < utc_end
            ],
        }
        for m in rows
    ]
    return PostInteractions(threads, posted_ids)
