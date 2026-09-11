from typing import Any

from components import SESSION_LOCAL, get_logger
from modules.ws import emit_ws_event

from ..conversation import load_recent_context_window
from ..llm import UserLlmConfig
from .persona_service import get_or_create_persona
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)

_MOOD_MAX_LEN = 200
_MAX_RESPONSE_TOKENS = 120

_MOOD_PROMPT_TEMPLATE = (
    "你是桌面伙伴的当前心情状态引擎。根据角色定义、已有心情和刚完成的陪伴对话，"
    "更新一句会展示在头像与名字下方的第一人称心情短语。\n"
    "这不是给用户的聊天回复，也不是桌面精灵的动作指令。不要输出 emotion、action、spatial、标签或旁白。\n"
    "心情要贴合角色性格与对话后的真实感受，简短、自然，避免复述助手刚说过的话。\n\n"
    "角色定义：\n{persona_extras}\n\n"
    "长期记忆：\n{memories_block}\n\n"
    "当前心情：{current_mood}\n\n"
    "最近对话：\n{recent_context}\n\n"
    "本轮用户消息：{user_message}\n"
    "本轮助手回复：{assistant_message}\n\n"
    '只返回 JSON：{{"mood": "第一人称心情短语"}}'
)


def normalize_mood(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    return text[:_MOOD_MAX_LEN]


async def emit_companion_mood(user_id: int, mood: str) -> None:
    """持久化角色当前心情，并通过独立状态事件刷新客户端身份轨。"""
    mood_text = normalize_mood(mood)
    if not mood_text:
        return

    async with SESSION_LOCAL() as db:
        persona = await get_or_create_persona(db, user_id)
        persona.current_mood = mood_text
        emit_ws_event(db, user_id=user_id, event_type="companion.mood", payload={"mood": mood_text})
        await db.commit()


async def update_mood_from_companion_turn(
    user_id: int,
    user_message: str,
    assistant_message: str,
    llm_config: UserLlmConfig | dict[str, Any],
) -> str | None:
    """在陪伴聊天完成后单独推理心情；结果不进入消息正文。"""
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return None

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or "暂无最近对话"

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        _MOOD_PROMPT_TEMPLATE,
        {
            "persona_extras": ctx.persona_extras,
            "memories_block": ctx.memories_block,
            "current_mood": ctx.current_mood or "尚未形成",
            "recent_context": recent_context,
            "user_message": user_message[-2000:],
            "assistant_message": assistant_message[-2000:],
        },
        max_output_tokens=_MAX_RESPONSE_TOKENS,
        log_prefix="mood_update",
    )
    if parsed is None:
        logger.info("mood_update: skipped", extra={"user_id": user_id, "reason": fail_reason})
        return None

    mood = normalize_mood(parsed.get("mood"))
    if mood is None:
        logger.info("mood_update: empty mood", extra={"user_id": user_id})
        return None

    await emit_companion_mood(user_id, mood)
    logger.info("mood_update: emitted", extra={"user_id": user_id})
    return mood
