"""聊天回合、委派和标题生成使用的会话数据入口。"""

from infrastructure.constants import DEFAULT_SESSION_TITLE
from infrastructure.database import session_scope
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.context_window import load_context_messages
from features.conversation.formatting import message_text
from features.conversation.main_conversation import get_or_create_special_conversation
from features.conversation.memory_scope import conversation_memory_scope
from features.conversation.models import Conversation, Message
from features.conversation.snapshots import ConversationSnapshot, MessageSnapshot


def conversation_snapshot(conv: Conversation) -> ConversationSnapshot:
    return ConversationSnapshot(
        conv.id,
        conv.user_id,
        conv.system_preset_id,
        conv.kind,
        conv.parent_id,
        conv.is_automation,
        conv.settings_json,
        conv.context_after_message_id,
        conv.title,
    )


def message_snapshot(row: Message) -> MessageSnapshot:
    return MessageSnapshot(
        row.id,
        row.conversation_id,
        row.role,
        row.subtype,
        row.content,
        row.tool_call_id,
        row.tool_calls,
        row.prompt_tokens,
        row.completion_tokens,
        row.turn_duration_ms,
        row.content_type,
        row.media_json,
        row.reply_json,
        row.reasoning_content,
        row.summary_through_message_id,
        row.created_at,
    )


async def turn_conversation(db: AsyncSession, session_id: str, user_id: int) -> ConversationSnapshot | None:
    row = await Conversation.by_session_id(db, session_id, user_id=user_id)
    return conversation_snapshot(row) if row is not None else None


async def ensure_special_conversation(
    db: AsyncSession,
    user_id: int,
    preset_id: str,
    *,
    commit: bool = True,
) -> ConversationSnapshot:
    row = await get_or_create_special_conversation(db, user_id, preset_id, commit=commit)
    return conversation_snapshot(row)


async def turn_message(db: AsyncSession, message_id: int) -> MessageSnapshot | None:
    row = await db.get(Message, message_id)
    return message_snapshot(row) if row is not None else None


async def context_messages(db: AsyncSession, conv: ConversationSnapshot) -> list[MessageSnapshot]:
    return [message_snapshot(row) for row in await load_context_messages(db, conv)]


async def conversation_topic_ids(db: AsyncSession, conv: ConversationSnapshot) -> tuple[int, ...]:
    """来源引用跨摘要保留，清空水位前的分享不再进入当前会话上下文。"""
    references = cast(Message.reply_json, JSONB)["used_topic_ids"]
    used_topics = await db.scalars(
        select(references)
        .distinct()
        .where(
            Message.conversation_id == conv.id,
            Message.id > conv.context_after_message_id,
            Message.content_type == "companion_reply",
            references != [],
        ),
    )
    return tuple(sorted({topic_id for topic_ids in used_topics for topic_id in topic_ids}))


async def create_delegated_conversation(user_id: int, parent_id: int | None) -> str | None:
    async with session_scope() as db:
        parent = await db.get(Conversation, parent_id) if parent_id else None
        if parent is None:
            return None
        conversation_memory_scope(parent, user_id)
        conv = Conversation(
            user_id=user_id,
            parent_id=parent_id,
            title="Subagent Task",
            system_preset_id=parent.system_preset_id,
            is_automation=parent.is_automation,
        )
        db.add(conv)
        await db.commit()
        return str(conv.id)


async def first_user_text(conversation_id: int) -> str:
    async with session_scope() as db:
        row = await db.scalar(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == "user")
            .order_by(Message.id)
            .limit(1),
        )
        return message_text(row) if row is not None else ""


async def update_default_title(conversation_id: int, title: str) -> bool:
    async with session_scope() as db:
        conv = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
        if conv is None or conv.title != DEFAULT_SESSION_TITLE:
            return False
        conv.title = title
        await db.commit()
        return True
