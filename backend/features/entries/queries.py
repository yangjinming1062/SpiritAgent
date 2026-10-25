"""内容摘要及关系批量读取，列表不加载评论正文。"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from infrastructure.text import ensure_utc
from sqlalchemy import Row, Select, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.service import asset_paths_in_session

from .contracts import CommentView, EntryError, EntryView
from .models import CompanionEntry, EntryAsset, EntryComment, EntryLink


def comment_view(row: EntryComment) -> CommentView:
    return CommentView(**{name: getattr(row, name) for name in CommentView.__dataclass_fields__})


def entry_view(
    row: CompanionEntry,
    *,
    media_url: str | None = None,
    audio_url: str | None = None,
    comment_count: int = 0,
    post_ids: tuple[int, ...] = (),
) -> EntryView:
    return EntryView(
        id=row.id,
        user_id=row.user_id,
        kind="diary" if row.kind == "diary" else "post",
        activity_date=row.activity_date,
        published_at=row.published_at,
        is_read=row.is_read,
        content_type=row.content_type,
        title=row.title,
        body=row.body,
        context_json=dict(row.context_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
        media_url=media_url,
        audio_url=audio_url,
        comment_count=comment_count,
        post_ids=post_ids,
    )


def entry_query() -> Select[Any]:
    assets = (
        select(func.jsonb_agg(func.jsonb_build_array(EntryAsset.role, EntryAsset.asset_id)))
        .where(
            EntryAsset.entry_id == CompanionEntry.id,
            EntryAsset.position == 0,
        )
        .correlate(CompanionEntry)
        .scalar_subquery()
    )
    links = (
        select(func.array_agg(aggregate_order_by(EntryLink.target_id, EntryLink.position)))
        .where(
            EntryLink.source_id == CompanionEntry.id,
            EntryLink.kind == "diary_source",
        )
        .correlate(CompanionEntry)
        .scalar_subquery()
    )
    comments = (
        select(func.count())
        .select_from(EntryComment)
        .where(
            EntryComment.post_id == CompanionEntry.id,
        )
        .correlate(CompanionEntry)
        .scalar_subquery()
    )
    return select(CompanionEntry, assets.label("assets"), links.label("links"), comments.label("comment_count"))


async def _entry_views(db: AsyncSession, user_id: int, rows: Sequence[Row[Any]]) -> list[EntryView]:
    asset_ids = {int(asset_id) for row in rows for _, asset_id in (row.assets or [])}
    paths = await asset_paths_in_session(db, user_id, asset_ids) if asset_ids else {}
    result = []
    for row in rows:
        roles = {role: paths.get(int(asset_id)) for role, asset_id in (row.assets or [])}
        result.append(
            entry_view(
                row[0],
                media_url=roles.get("main"),
                audio_url=roles.get("audio"),
                comment_count=row.comment_count,
                post_ids=tuple(row.links or ()),
            )
        )
    return result


async def read_entries(db: AsyncSession, user_id: int, statement: Select[Any]) -> list[EntryView]:
    rows = list((await db.execute(statement.where(CompanionEntry.user_id == user_id))).all())
    return await _entry_views(db, user_id, rows)


async def get_entry(db: AsyncSession, user_id: int, entry_id: int, *, kind: str | None = None) -> EntryView | None:
    statement = entry_query().where(CompanionEntry.id == entry_id)
    if kind is not None:
        statement = statement.where(CompanionEntry.kind == kind)
    rows = await read_entries(db, user_id, statement)
    return rows[0] if rows else None


async def list_posts(
    db: AsyncSession, user_id: int, *, cursor: str | None = None, limit: int = 20
) -> tuple[list[EntryView], str | None, list[int]]:
    unread = (
        select(func.array_agg(CompanionEntry.id))
        .where(CompanionEntry.user_id == user_id, CompanionEntry.kind == "post", CompanionEntry.is_read.is_(False))
        .correlate(None)
        .scalar_subquery()
    )
    statement = (
        entry_query()
        .add_columns(unread.label("unread_ids"))
        .where(CompanionEntry.user_id == user_id, CompanionEntry.kind == "post")
    )
    if cursor is not None:
        try:
            boundary = decode_timestamp_cursor(cursor)
            if boundary is None:
                raise ValueError("Invalid cursor")
            stamp, row_id = boundary
        except (ValueError, TypeError) as exc:
            raise EntryError("无效的动态分页位置") from exc
        statement = statement.where(
            tuple_(CompanionEntry.published_at, CompanionEntry.id) < (ensure_utc(stamp), row_id)
        )
    raw_rows = list(
        (
            await db.execute(
                statement.order_by(CompanionEntry.published_at.desc(), CompanionEntry.id.desc()).limit(limit + 1)
            )
        ).all()
    )
    unread_ids = list(raw_rows[0].unread_ids or ()) if raw_rows else []
    rows = await _entry_views(db, user_id, raw_rows)
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_timestamp_cursor(rows[-1].published_at, rows[-1].id) if more else None
    return rows, next_cursor, unread_ids


async def list_diary_entries(
    db: AsyncSession, user_id: int, *, date_from: date | None = None, date_to: date | None = None, limit: int = 100
) -> list[EntryView]:
    statement = entry_query().where(CompanionEntry.kind == "diary")
    if date_from is not None:
        statement = statement.where(CompanionEntry.activity_date >= date_from)
    if date_to is not None:
        statement = statement.where(CompanionEntry.activity_date <= date_to)
    return await read_entries(
        db, user_id, statement.order_by(CompanionEntry.activity_date.desc()).limit(max(1, min(365, limit)))
    )


async def diary_for_date(db: AsyncSession, user_id: int, entry_date: date) -> EntryView | None:
    rows = await read_entries(
        db, user_id, entry_query().where(CompanionEntry.kind == "diary", CompanionEntry.activity_date == entry_date)
    )
    return rows[0] if rows else None


async def diary_texts(db: AsyncSession, user_id: int) -> list[tuple[date, str, str]]:
    rows = await db.execute(
        select(CompanionEntry.activity_date, CompanionEntry.title, CompanionEntry.body)
        .where(CompanionEntry.user_id == user_id, CompanionEntry.kind == "diary")
        .order_by(CompanionEntry.activity_date)
    )
    return list(rows.tuples())


async def unread_entry_ids(db: AsyncSession, user_id: int, kind: Literal["post", "diary"]) -> list[int]:
    return list(
        await db.scalars(
            select(CompanionEntry.id).where(
                CompanionEntry.user_id == user_id, CompanionEntry.kind == kind, CompanionEntry.is_read.is_(False)
            )
        )
    )


async def has_unread_entries(db: AsyncSession, user_id: int, kind: Literal["post", "diary"]) -> bool:
    return (
        await db.scalar(
            select(CompanionEntry.id)
            .where(CompanionEntry.user_id == user_id, CompanionEntry.kind == kind, CompanionEntry.is_read.is_(False))
            .limit(1)
        )
        is not None
    )


async def count_posts_since(db: AsyncSession, user_id: int, since: datetime) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(CompanionEntry)
            .where(
                CompanionEntry.user_id == user_id, CompanionEntry.kind == "post", CompanionEntry.published_at >= since
            )
        )
        or 0
    )


async def recent_posts(
    db: AsyncSession,
    user_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[EntryView]:
    statement = entry_query().where(CompanionEntry.kind == "post")
    if since is not None:
        statement = statement.where(CompanionEntry.published_at >= since)
    if until is not None:
        statement = statement.where(CompanionEntry.published_at <= until)
    return await read_entries(
        db, user_id, statement.order_by(CompanionEntry.published_at.desc(), CompanionEntry.id.desc()).limit(limit)
    )


async def post_summaries(db: AsyncSession, user_id: int, limit: int) -> list[tuple[datetime, str, str]]:
    rows = await db.execute(
        select(CompanionEntry.published_at, CompanionEntry.content_type, CompanionEntry.title)
        .where(CompanionEntry.user_id == user_id, CompanionEntry.kind == "post")
        .order_by(CompanionEntry.published_at.desc(), CompanionEntry.id.desc())
        .limit(limit)
    )
    return list(rows.tuples())


async def comments_for_entries(
    db: AsyncSession, user_id: int, entry_ids: Sequence[int], *, before: datetime | None = None
) -> dict[int, list[CommentView]]:
    if not entry_ids:
        return {}
    statement = select(EntryComment).where(EntryComment.user_id == user_id, EntryComment.post_id.in_(entry_ids))
    if before is not None:
        statement = statement.where(EntryComment.created_at < before)
    result: dict[int, list[CommentView]] = {}
    for row in await db.scalars(statement.order_by(EntryComment.created_at, EntryComment.id)):
        result.setdefault(row.post_id, []).append(comment_view(row))
    return result


async def list_comments(
    db: AsyncSession, user_id: int, post_id: int, *, cursor: str | None = None, limit: int = 50
) -> tuple[list[CommentView], str | None]:
    statement = select(EntryComment).where(EntryComment.user_id == user_id, EntryComment.post_id == post_id)
    if cursor is not None:
        try:
            boundary = decode_timestamp_cursor(cursor)
            if boundary is None:
                raise ValueError("Invalid cursor")
            stamp, row_id = boundary
        except (ValueError, TypeError) as exc:
            raise EntryError("无效的评论分页位置") from exc
        statement = statement.where(tuple_(EntryComment.created_at, EntryComment.id) > (ensure_utc(stamp), row_id))
    rows = list(await db.scalars(statement.order_by(EntryComment.created_at, EntryComment.id).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_timestamp_cursor(rows[-1].created_at, rows[-1].id) if more else None
    return [comment_view(row) for row in rows], next_cursor


async def interaction_entries(
    db: AsyncSession, user_id: int, local_date: date, utc_start: datetime, utc_end: datetime
) -> list[EntryView]:
    active_comment = (
        select(EntryComment.id)
        .where(
            EntryComment.post_id == CompanionEntry.id,
            EntryComment.created_at >= utc_start,
            EntryComment.created_at < utc_end,
        )
        .exists()
    )
    return await read_entries(
        db,
        user_id,
        entry_query()
        .where(CompanionEntry.kind == "post", or_(CompanionEntry.activity_date == local_date, active_comment))
        .order_by(CompanionEntry.published_at, CompanionEntry.id),
    )


async def pending_reply(db: AsyncSession, user_id: int, post_id: int) -> CommentView | None:
    row = await db.scalar(
        select(EntryComment)
        .where(
            EntryComment.user_id == user_id,
            EntryComment.post_id == post_id,
            EntryComment.role == "user",
            EntryComment.reply_status.in_(("pending", "running")),
        )
        .order_by(EntryComment.created_at, EntryComment.id)
        .limit(1)
    )
    return comment_view(row) if row else None


async def pending_reply_threads(db: AsyncSession) -> list[tuple[int, int]]:
    result = await db.execute(
        select(EntryComment.user_id, EntryComment.post_id)
        .where(EntryComment.role == "user", EntryComment.reply_status.in_(("pending", "running")))
        .distinct()
    )
    return list(result.tuples())


async def entry_asset_paths(db: AsyncSession, user_id: int) -> set[str]:
    ids = list(await db.scalars(select(EntryAsset.asset_id).where(EntryAsset.user_id == user_id).distinct()))
    return set((await asset_paths_in_session(db, user_id, ids)).values()) if ids else set()
