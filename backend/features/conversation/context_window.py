from dataclasses import dataclass
from datetime import datetime, timedelta

from infrastructure.text import utc_now
from pydantic import JsonValue
from runtime.presets import COMPANION_PRESET_ID
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.timezone import resolve_user_timezone
from features.conversation.formatting import companion_context_items, format_messages_compact
from features.conversation.main_conversation import UI_ONLY_SUBTYPES
from features.conversation.memory_scope import user_authored_conversation
from features.conversation.models import PRIVATE_CHECKPOINT_SUBTYPE, Conversation, Message
from features.conversation.snapshots import ConversationSnapshot

CHECKPOINT_SUBTYPE = "compress_summary"
CHECKPOINT_SUBTYPES = frozenset({CHECKPOINT_SUBTYPE, PRIVATE_CHECKPOINT_SUBTYPE})


def context_visible_subtype() -> ColumnElement[bool]:
    return Message.subtype.is_(None) | Message.subtype.notin_((*UI_ONLY_SUBTYPES, *CHECKPOINT_SUBTYPES))


async def _context_checkpoint(
    db: AsyncSession, conv: Conversation | ConversationSnapshot
) -> tuple[Message | None, int]:
    checkpoint = await db.scalar(
        select(Message)
        .where(
            Message.conversation_id == conv.id,
            Message.id > conv.context_after_message_id,
            Message.subtype.in_(CHECKPOINT_SUBTYPES),
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    watermark = conv.context_after_message_id
    if checkpoint is not None:
        boundary = await db.scalar(
            select(Message.id).where(
                Message.conversation_id == conv.id,
                Message.id == checkpoint.summary_through_message_id,
                Message.subtype.is_(None) | Message.subtype.notin_(CHECKPOINT_SUBTYPES),
            )
        )
        if boundary is None:
            raise ValueError("Conversation summary requires an original message boundary")
        watermark = max(watermark, boundary)
    return checkpoint, watermark


async def context_message_watermark(db: AsyncSession, conv: Conversation | ConversationSnapshot) -> int:
    return (await _context_checkpoint(db, conv))[1]


async def load_context_messages(db: AsyncSession, conv: Conversation | ConversationSnapshot) -> list[Message]:
    """按摘要的原消息覆盖边界读取未总结的历史，按消息 id 排序。"""
    checkpoint, watermark = await _context_checkpoint(db, conv)
    stmt = select(Message).where(Message.conversation_id == conv.id, Message.id > watermark, context_visible_subtype())
    rows = list((await db.scalars(stmt.order_by(Message.id))).all())
    return [checkpoint, *rows] if checkpoint is not None else rows


@dataclass(frozen=True)
class DeliveredLifeExcerpt:
    excerpt_id: int
    delivered_at: datetime
    bubbles: list[dict[str, JsonValue]]


async def delivered_life_excerpts(
    db: AsyncSession, user_id: int, conversation_id: int | None = None
) -> list[DeliveredLifeExcerpt]:
    query = (
        select(Message)
        .join(Conversation)
        .where(
            Message.user_id == user_id,
            Message.life_excerpt_id.is_not(None),
            Message.role == "assistant",
            Message.content_type == "companion_reply",
            Message.id > Conversation.context_after_message_id,
            user_authored_conversation(),
            Conversation.system_preset_id == COMPANION_PRESET_ID,
        )
    )
    if conversation_id is not None:
        query = query.where(Conversation.id == conversation_id)
    rows = await db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(12))
    return [
        DeliveredLifeExcerpt(row.life_excerpt_id, row.created_at, companion_context_items(row, dialogue_only=True))
        for row in rows
        if row.life_excerpt_id is not None
    ]


async def load_recent_companion_exchange(
    db: AsyncSession, user_id: int, *, conversation_id: int | None = None, current_interaction: bool = True
) -> str:
    """即时状态参考半小时内的互动。关系判断保留最近的带日期往来，可以跨日及同预设顶层会话。"""
    now = utc_now()
    window = [Message.created_at >= now - timedelta(minutes=30)] if current_interaction else []
    if conversation_id is not None:
        window.append(Conversation.id == conversation_id)
    elif current_interaction:
        window.append(Conversation.kind == "special")
    rows = await db.scalars(
        select(Message)
        .join(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.system_preset_id == COMPANION_PRESET_ID,
            user_authored_conversation(),
            Message.id > Conversation.context_after_message_id,
            Message.role.in_(("user", "assistant")),
            context_visible_subtype(),
            Message.tool_calls.is_(None),
            Message.created_at <= now,
            *window,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(16)
    )
    return format_messages_compact(
        list(reversed(rows.all())),
        user_local_tz=await resolve_user_timezone(db, user_id),
        max_content_chars=None if current_interaction else 600,
    )
