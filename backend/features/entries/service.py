"""内容和评论的事务内操作；事务提交由业务用例负责。"""

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from runtime.events.emit import emit_ws_event
from sqlalchemy import BigInteger, String, column, delete, literal, select, text, true, update, values
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import ensure_assets_in_session

from .contracts import CommentView, EntryError, EntryNotFoundError, EntryView
from .models import CompanionEntry, EntryAsset, EntryComment, EntryLink
from .queries import comment_view, diary_for_date, entry_view, has_unread_entries


def emit_comment(db: AsyncSession, row: CommentView) -> None:
    emit_ws_event(
        db,
        user_id=row.user_id,
        event_type="companion.post.comment",
        payload={
            "post_id": str(row.post_id),
            "comment": {
                "id": str(row.id),
                "post_id": str(row.post_id),
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "reply_to_comment_id": str(row.reply_to_comment_id) if row.reply_to_comment_id is not None else None,
                "reply_status": row.reply_status,
                "reply_error": row.reply_error,
            },
        },
    )


async def create_post(
    db: AsyncSession,
    user_id: int,
    *,
    activity_date: date,
    title: str,
    body: str,
    content_type: str,
    media_url: str | None,
    audio_url: str | None,
    context: dict[str, Any],
) -> EntryView:
    paths = {role: path for role, path in (("main", media_url), ("audio", audio_url)) if path is not None}
    assets = await ensure_assets_in_session(db, user_id, list(paths.values())) if paths else {}
    statement = (
        insert(CompanionEntry)
        .values(
            user_id=user_id,
            kind="post",
            activity_date=activity_date,
            title=title,
            body=body,
            content_type=content_type,
            context_json=context,
        )
        .returning(CompanionEntry)
    )
    if paths:
        created = statement.cte("created_entry")
        attachments = (
            values(column("role", String), column("asset_id", BigInteger), column("source_key", String))
            .data([(role, assets[path], str(uuid4())) for role, path in paths.items()])
            .alias("attachments")
        )
        linked = (
            insert(EntryAsset.__table__)
            .from_select(
                ["user_id", "entry_id", "asset_id", "role", "position", "source_key"],
                select(
                    literal(user_id),
                    created.c.id,
                    attachments.c.asset_id,
                    attachments.c.role,
                    literal(0),
                    attachments.c.source_key,
                ).select_from(created.join(attachments, true())),
            )
            .cte("linked_assets")
        )
        row = await db.scalar(select(CompanionEntry).from_statement(select(created).add_cte(linked)))
    else:
        row = await db.scalar(statement)
    if row is None:
        raise RuntimeError("Content insert returned no row")
    return entry_view(row, media_url=media_url, audio_url=audio_url)


async def create_diary(
    db: AsyncSession,
    user_id: int,
    *,
    entry_date: date,
    title: str,
    body: str,
    post_ids: Sequence[int],
) -> tuple[EntryView, bool]:
    targets = list(dict.fromkeys(post_ids))
    if targets:
        allowed = set(
            await db.scalars(
                select(CompanionEntry.id).where(
                    CompanionEntry.user_id == user_id, CompanionEntry.kind == "post", CompanionEntry.id.in_(targets)
                )
            )
        )
        if allowed != set(targets):
            raise EntryError("日记只能引用本账户已发布的动态")
    row = await db.scalar(
        insert(CompanionEntry)
        .values(
            user_id=user_id,
            kind="diary",
            activity_date=entry_date,
            title=title,
            body=body,
            content_type="text",
            context_json={},
        )
        .on_conflict_do_nothing(index_elements=["user_id", "activity_date"], index_where=text("kind = 'diary'"))
        .returning(CompanionEntry)
    )
    if row is None:
        existing = await diary_for_date(db, user_id, entry_date)
        if existing is None:
            raise RuntimeError("Conflicting diary disappeared")
        return existing, False
    if targets:
        await db.execute(
            insert(EntryLink).values(
                [
                    {
                        "user_id": user_id,
                        "source_id": row.id,
                        "target_id": target,
                        "kind": "diary_source",
                        "position": position,
                    }
                    for position, target in enumerate(targets)
                ]
            )
        )
    return entry_view(row, post_ids=tuple(targets)), True


async def mark_entries_read(db: AsyncSession, user_id: int, kind: Literal["post", "diary"], ids: Sequence[int]) -> bool:
    changed = (
        list(
            await db.scalars(
                update(CompanionEntry)
                .where(
                    CompanionEntry.user_id == user_id,
                    CompanionEntry.kind == kind,
                    CompanionEntry.id.in_(ids),
                    CompanionEntry.is_read.is_(False),
                )
                .values(is_read=True)
                .returning(CompanionEntry.id)
            )
        )
        if ids
        else []
    )
    if changed:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.posts.read" if kind == "post" else "companion.diary.read",
            payload={},
        )
    return await has_unread_entries(db, user_id, kind)


async def delete_diary(db: AsyncSession, user_id: int, entry_date: date) -> int | None:
    return await db.scalar(
        delete(CompanionEntry)
        .where(
            CompanionEntry.user_id == user_id,
            CompanionEntry.kind == "diary",
            CompanionEntry.activity_date == entry_date,
        )
        .returning(CompanionEntry.id)
    )


async def create_user_comment(db: AsyncSession, user_id: int, post_id: int, content: str) -> CommentView:
    content = content.strip()
    if not content or len(content) > 500:
        raise EntryError("评论需要1至500字符")
    if (
        await db.scalar(
            select(CompanionEntry.id).where(
                CompanionEntry.id == post_id, CompanionEntry.user_id == user_id, CompanionEntry.kind == "post"
            )
        )
        is None
    ):
        raise EntryNotFoundError("找不到动态")
    row = await db.scalar(
        insert(EntryComment)
        .values(
            user_id=user_id, post_id=post_id, role="user", content=content, reply_status="pending", reply_attempts=0
        )
        .returning(EntryComment)
    )
    if row is None:
        raise RuntimeError("Comment insert returned no row")
    view = comment_view(row)
    emit_comment(db, view)
    return view


async def delete_user_comment(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> None:
    target = select(EntryComment.id).where(
        EntryComment.id == comment_id,
        EntryComment.user_id == user_id,
        EntryComment.post_id == post_id,
        EntryComment.role == "user",
    )
    await db.execute(
        update(EntryComment)
        .where(
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.reply_to_comment_id.in_(target),
        )
        .values(reply_to_comment_id=None)
    )
    removed = await db.scalar(
        delete(EntryComment)
        .where(
            EntryComment.id == comment_id,
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.role == "user",
        )
        .returning(EntryComment.id)
    )
    if removed is None:
        raise EntryNotFoundError("找不到评论")
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.post.comment.deleted",
        payload={"post_id": str(post_id), "comment_id": str(comment_id)},
    )


async def retry_comment_reply(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> CommentView:
    row = await db.scalar(
        update(EntryComment)
        .where(
            EntryComment.id == comment_id,
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.role == "user",
            EntryComment.reply_status == "failed",
        )
        .values(reply_status="pending", reply_error=None)
        .returning(EntryComment)
    )
    if row is None:
        raise EntryError("评论不存在或回复尚不可重试")
    view = comment_view(row)
    emit_comment(db, view)
    return view


async def begin_reply(db: AsyncSession, user_id: int, post_id: int, comment_id: int) -> CommentView | None:
    row = await db.scalar(
        update(EntryComment)
        .where(
            EntryComment.id == comment_id,
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.role == "user",
            EntryComment.reply_status.in_(("pending", "running")),
        )
        .values(reply_status="running", reply_error=None, reply_attempts=EntryComment.reply_attempts + 1)
        .returning(EntryComment)
    )
    if row is None:
        return None
    view = comment_view(row)
    emit_comment(db, view)
    return view


async def fail_reply(db: AsyncSession, user_id: int, comment_id: int) -> None:
    row = await db.scalar(
        update(EntryComment)
        .where(
            EntryComment.id == comment_id,
            EntryComment.user_id == user_id,
            EntryComment.reply_status.in_(("pending", "running")),
        )
        .values(reply_status="failed", reply_error="回复暂未完成，可以重试")
        .returning(EntryComment)
    )
    if row is not None:
        emit_comment(db, comment_view(row))


async def finish_reply(
    db: AsyncSession, user_id: int, post_id: int, comment_id: int, attempt: int, content: str
) -> None:
    target = await db.scalar(
        update(EntryComment)
        .where(
            EntryComment.id == comment_id,
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.reply_attempts == attempt,
            EntryComment.reply_status == "running",
        )
        .values(reply_status="completed", reply_error=None)
        .returning(EntryComment)
    )
    if target is None:
        return
    reply = await db.scalar(
        insert(EntryComment)
        .values(
            user_id=user_id,
            post_id=post_id,
            role="companion",
            content=content,
            reply_to_comment_id=comment_id,
            reply_status="none",
        )
        .on_conflict_do_nothing(index_elements=["reply_to_comment_id"])
        .returning(EntryComment)
    )
    if reply is not None:
        emit_comment(db, comment_view(reply))
    emit_comment(db, comment_view(target))
