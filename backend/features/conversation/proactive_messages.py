"""在调用方事务中保存主动回复及媒体绑定；陪伴策略由调用方决定。"""

from datetime import datetime
from uuid import UUID

from infrastructure.domain_locks import lock_domain
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from runtime.presets import COMPANION_PRESET_ID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.topics.store import record_topic_usage

from .asset_bindings import flush_conversation
from .main_conversation import get_or_create_main_conversation
from .models import Conversation, Message
from .replies import CompanionReply
from .reply_audio import client_reply_bubbles
from .reply_media import bind_reply_videos


async def append_companion_message(
    db: AsyncSession,
    user_id: int,
    reply: CompanionReply,
    content: str,
    *,
    conversation_id: int | None = None,
    life_excerpt_id: int | None = None,
    gesture_request_id: UUID | None = None,
    gesture_at: datetime | None = None,
    read_aloud: bool = False,
) -> bool:
    reply.validate_content(content)
    if gesture_request_id is not None:
        await lock_domain(db, user_id, "gesture_message")
        if await db.scalar(
            select(Message.id).where(Message.user_id == user_id, Message.gesture_request_id == gesture_request_id)
        ):
            return False
    conversation = (
        await Conversation.by_session_id(db, str(conversation_id), user_id=user_id)
        if conversation_id is not None
        else await get_or_create_main_conversation(db, user_id, commit=False)
    )
    if (
        conversation is None
        or conversation.system_preset_id != COMPANION_PRESET_ID
        or conversation.parent_id is not None
        or conversation.is_automation
    ):
        raise LookupError("Companion conversation is no longer available")
    message = Message(
        user_id=conversation.user_id,
        conversation_id=conversation.id,
        role="assistant",
        content=content,
        content_type="companion_reply",
        reply_json=reply.model_dump(mode="json"),
        subtype="status_proactive",
        gesture_request_id=gesture_request_id,
        life_excerpt_id=life_excerpt_id,
    )
    db.add(message)
    await flush_conversation(db)
    await bind_reply_videos(db, message, reply, user_id)
    await record_topic_usage(
        db, MemoryScope(user_id, "companion"), reply.used_topic_ids, message.id, authorized_at=reply.topic_context_at
    )
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.message",
        payload={
            "bubbles": client_reply_bubbles(reply),
            **({"gesture_at": gesture_at.isoformat(), "read_aloud": read_aloud} if gesture_at is not None else {}),
            "session_id": str(conversation.id),
            "message_id": message.id,
            "is_main": conversation.kind == "special",
        },
    )
    return True
