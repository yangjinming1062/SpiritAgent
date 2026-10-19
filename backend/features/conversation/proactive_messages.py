"""在调用方事务中保存主动回复及媒体绑定；陪伴策略由调用方决定。"""

from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy.ext.asyncio import AsyncSession

from features.topics.store import record_topic_usage

from .main_conversation import get_or_create_main_conversation
from .models import Message
from .replies import CompanionReply
from .reply_audio import client_reply_bubbles
from .reply_media import bind_reply_videos


async def append_companion_message(db: AsyncSession, user_id: int, reply: CompanionReply, content: str) -> None:
    reply.validate_content(content)
    conversation = await get_or_create_main_conversation(db, user_id, commit=False)
    message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=content,
        content_type="companion_reply",
        reply_json=reply.model_dump_json(),
        subtype="status_proactive",
    )
    db.add(message)
    await db.flush()
    await bind_reply_videos(db, message, reply, user_id)
    await record_topic_usage(
        db,
        MemoryScope(user_id, "companion"),
        reply.used_topic_ids,
        message.id,
        authorized_at=reply.topic_context_at,
    )
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.message",
        payload={"bubbles": client_reply_bubbles(reply), "session_id": str(conversation.id), "message_id": message.id},
    )
