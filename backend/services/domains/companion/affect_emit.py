from components import SESSION_LOCAL
from modules.conversation import Message
from modules.ws import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import (
    get_or_create_special_conversation,
)

from .proactive_runtime import note_outreach_throttle


async def emit_companion_affect(user_id: int, emotion: str | None = None, *, actions: list[str] | None = None) -> None:
    """广播桌面精灵的结构化视觉表达。"""
    emotion_token = emotion if emotion and emotion != "neutral" else None
    action_tokens = [action for action in (actions or []) if action][:3]
    if not emotion_token and not action_tokens:
        return

    payload: dict[str, object] = {}
    if emotion_token:
        payload["emotion"] = emotion_token
    if action_tokens:
        payload["actions"] = action_tokens

    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type="companion.affect", payload=payload)
        await db.commit()


async def append_companion_message(db: AsyncSession, user_id: int, text: str) -> None:
    main_conv = await get_or_create_special_conversation(db, user_id, "companion", commit=False)
    message = Message(conversation_id=main_conv.id, role="assistant", content=text, subtype="status_proactive")
    db.add(message)
    await db.flush()
    emit_ws_event(
        db,
        user_id=user_id,
        event_type="companion.message",
        payload={
            "text": text,
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
        await append_companion_message(db, user_id, clean_text)
        await db.commit()
    note_outreach_throttle(user_id)
