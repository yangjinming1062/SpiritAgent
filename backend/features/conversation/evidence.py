"""记忆审阅与陪伴触发消费的原始发言快照和事务内水位操作。"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .context_window import CHECKPOINT_SUBTYPE
from .formatting import message_contains_text, message_text
from .main_conversation import UI_ONLY_SUBTYPES
from .memory_scope import user_authored_conversation
from .models import Conversation, Message


@dataclass(frozen=True)
class OriginalMessageSnapshot:
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime


def _reviewable_message() -> ColumnElement[bool]:
    return and_(
        user_authored_conversation(),
        Message.id > Conversation.context_after_message_id,
        Message.role.in_(("user", "assistant")),
        Message.subtype.is_(None),
        Message.tool_calls.is_(None),
    )


async def review_scopes(db: AsyncSession, active_user_ids: Collection[int]) -> list[tuple[int, str]]:
    rows = await db.execute(
        select(Conversation.user_id, Conversation.system_preset_id)
        .join(Message)
        .where(
            Conversation.user_id.in_(active_user_ids),
            _reviewable_message(),
            Message.id > Conversation.memory_reviewed_message_id,
        )
        .distinct(),
    )
    return list(rows.tuples().all())


async def review_messages(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    new_only: bool = False,
    query: str | None = None,
    limit: int,
) -> list[OriginalMessageSnapshot]:
    statement = (
        select(Message)
        .join(Conversation)
        .where(Conversation.user_id == user_id, Conversation.system_preset_id == preset_id, _reviewable_message())
    )
    if session_id is not None:
        statement = statement.where(Conversation.id == session_id)
    if through_message_id is not None:
        statement = statement.where(Message.id <= through_message_id)
    if new_only:
        statement = statement.where(Message.id > Conversation.memory_reviewed_message_id)
    if query:
        statement = statement.where(message_contains_text(query))
    rows = await db.scalars(statement.order_by(Message.id.asc() if new_only else Message.id.desc()).limit(limit))
    return [
        OriginalMessageSnapshot(row.id, row.conversation_id, row.role, message_text(row), row.created_at)
        for row in rows
    ]


async def original_user_messages(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    message_ids: Collection[int],
) -> dict[int, OriginalMessageSnapshot]:
    rows = await db.scalars(
        select(Message)
        .join(Conversation)
        .where(
            Message.id.in_(message_ids),
            Message.role == "user",
            Message.subtype.is_(None),
            Conversation.user_id == user_id,
            Conversation.system_preset_id == preset_id,
            user_authored_conversation(),
            Message.id > Conversation.context_after_message_id,
        ),
    )
    return {
        row.id: OriginalMessageSnapshot(row.id, row.conversation_id, row.role, message_text(row), row.created_at)
        for row in rows
    }


async def source_conversation_exists(db: AsyncSession, user_id: int, preset_id: str, session_id: int) -> bool:
    return (
        await db.scalar(
            select(Conversation.id).where(
                Conversation.id == session_id,
                Conversation.user_id == user_id,
                Conversation.system_preset_id == preset_id,
                Conversation.is_automation.is_(False),
            ),
        )
        is not None
    )


async def latest_scope_message_id(db: AsyncSession, user_id: int, preset_id: str) -> int:
    return (
        await db.scalar(
            select(func.max(Message.id))
            .join(Conversation)
            .where(Conversation.user_id == user_id, Conversation.system_preset_id == preset_id),
        )
        or 0
    )


async def latest_user_message_id(db: AsyncSession, user_id: int) -> int:
    return (
        await db.scalar(
            select(func.max(Message.id))
            .join(Conversation)
            .where(Conversation.user_id == user_id, user_authored_conversation(), Message.role == "user"),
        )
        or 0
    )


async def advance_memory_review_cursor(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    session_id: int,
    message_id: int,
) -> None:
    await db.execute(
        Conversation.__table__.update()
        .where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
            Conversation.system_preset_id == preset_id,
        )
        .values(memory_reviewed_message_id=func.greatest(Conversation.memory_reviewed_message_id, message_id)),
    )


async def companion_conversation_users(db: AsyncSession, user_ids: Collection[int], preset_id: str) -> set[int]:
    return set(
        await db.scalars(
            select(Conversation.user_id)
            .where(
                Conversation.user_id.in_(user_ids),
                Conversation.system_preset_id == preset_id,
                user_authored_conversation(),
            )
            .distinct(),
        ),
    )


def _nightly_scope(user_id: int, preset_id: str) -> ColumnElement[bool]:
    return and_(
        Conversation.user_id == user_id,
        Conversation.system_preset_id == preset_id,
        Message.id > Conversation.context_after_message_id,
        user_authored_conversation(),
        Message.role.in_(("user", "assistant")),
        Message.subtype.is_(None) | Message.subtype.notin_((*UI_ONLY_SUBTYPES, CHECKPOINT_SUBTYPE)),
        Message.tool_calls.is_(None),
    )


async def nightly_messages(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    start: datetime,
    end: datetime,
) -> list[OriginalMessageSnapshot]:
    rows = await db.scalars(
        select(Message)
        .join(Conversation)
        .where(
            _nightly_scope(user_id, preset_id),
            Message.created_at >= start,
            Message.created_at < end,
        )
        .order_by(Message.created_at, Message.id),
    )
    return [
        OriginalMessageSnapshot(row.id, row.conversation_id, row.role, message_text(row), row.created_at)
        for row in rows
    ]


async def nightly_user_message_count(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    start: datetime,
    end: datetime,
) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation)
            .where(
                _nightly_scope(user_id, preset_id),
                Message.role == "user",
                Message.created_at >= start,
                Message.created_at < end,
            ),
        )
        or 0
    )
