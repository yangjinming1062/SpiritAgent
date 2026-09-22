from components import SESSION_LOCAL
from modules.conversation import CompanionReply, Message, TextBubble
from modules.ws import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import (
    client_reply_bubbles,
    get_or_create_special_conversation,
)

from .proactive_runtime import note_outreach_throttle


async def append_companion_message(db: AsyncSession, user_id: int, reply: CompanionReply) -> None:
    text = reply.dialogue()
    main_conv = await get_or_create_special_conversation(db, user_id, "companion", commit=False)
    message = Message(
        conversation_id=main_conv.id,
        role="assistant",
        content=text,
        reply_json=reply.model_dump_json(),
        subtype="status_proactive",
    )
    db.add(message)
    await db.flush()
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.message",
        payload={
            "text": text,
            "bubbles": client_reply_bubbles(reply),
            "session_id": str(main_conv.id),
            "message_id": message.id,
        },
    )


async def emit_companion_message(user_id: int, text: str) -> None:
    """把伙伴主动消息推送到客户端（WSEvent companion.message）并落库。

    供 send_message_tool（LLM 主动触达工具）与 should_act 的 approach（走过去搭话）共用：
    是否展示由客户端打扰档位决定，静止档的源头拦截由调用方各自负责。
    """
    clean_text = text.strip()
    if not clean_text:
        return
    async with SESSION_LOCAL() as db:
        await append_companion_message(db, user_id, CompanionReply(bubbles=[TextBubble(type="text", text=clean_text)]))
        await db.commit()
    note_outreach_throttle(user_id)
