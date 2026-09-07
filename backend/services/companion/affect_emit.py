from components import SESSION_LOCAL
from modules.conversation import Message
from modules.ws import emit_ws_event

from services.conversation import (
    get_or_create_special_conversation,
    record_user_outreach,
)

from .persona_service import get_or_create_persona

_MOOD_MAX_LEN = 200


def clip_mood(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    return text[:_MOOD_MAX_LEN]


async def emit_companion_affect(user_id: int, emotion: str | None = None, *, mood: str | None = None) -> None:
    """广播情境情绪与心境：非中性情绪切视觉状态，心境说明持久化后随事件下发。"""
    mood_text = clip_mood(mood)
    emotion_token = emotion if emotion and emotion != "neutral" else None
    if not mood_text and not emotion_token:
        return

    payload: dict[str, str] = {}
    if emotion_token:
        payload["emotion"] = emotion_token
    if mood_text:
        payload["mood"] = mood_text

    async with SESSION_LOCAL() as db:
        if mood_text:
            persona = await get_or_create_persona(db, user_id)
            persona.current_mood = mood_text
        emit_ws_event(db, user_id=user_id, event_type="companion.affect", payload=payload)
        await db.commit()


async def emit_companion_message(
    user_id: int,
    text: str,
    affect: str | None = None,
    followup_timeout_seconds: float | None = None,
) -> None:
    """把伙伴主动消息推送到客户端（WSEvent companion.message）并落库。

    供 send_message_tool（LLM 主动触达工具）与 should_act 的 approach（走过去搭话）共用：
    是否展示由客户端打扰档位决定，静止档的源头拦截由调用方各自负责。
    """
    payload: dict[str, object] = {"text": text}
    if affect:
        payload["affect"] = {"emotion": affect}
    async with SESSION_LOCAL() as db:
        emit_ws_event(db, user_id=user_id, event_type="companion.message", payload=payload)
        # status_proactive 留在 LLM 上下文中（用户可回复），空消息不应在那里累积出一段空白对话回合。
        if text.strip():
            main_conv = await get_or_create_special_conversation(db, user_id, "companion")
            db.add(Message(conversation_id=main_conv.id, role="assistant", content=text, subtype="status_proactive"))
            record_user_outreach(user_id, text.strip(), followup_timeout_seconds)
        await db.commit()
