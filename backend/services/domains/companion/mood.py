from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, get_logger, resolve_prompt_text
from modules.ws import emit_ws_event
from prompts.companion import MOOD_INSTRUCTIONS

from services.domains.conversation import load_recent_context_window
from services.infrastructure.llm import UserLlmConfig

from .persona_service import get_or_create_persona
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)

_MOOD_MAX_LEN = 200


def normalize_mood(raw: object) -> str | None:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text or len(text) > _MOOD_MAX_LEN:
        return None
    return text


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
    llm_config: UserLlmConfig,
) -> str | None:
    """在陪伴聊天完成后单独推理心情；结果不进入消息正文。"""
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return None

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(MOOD_INSTRUCTIONS, ctx.language),
        {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "user_message": user_message[-2000:],
            "assistant_message": assistant_message[-2000:],
            "user_message_truncated": len(user_message) > 2000,
            "assistant_message_truncated": len(assistant_message) > 2000,
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
            **({"current_mood": ctx.current_mood} if ctx.current_mood else {}),
            **({"recent_context": recent_context} if recent_context else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="mood_update",
        temperature=0.5,
    )
    if parsed is None:
        logger.info("mood_update: skipped", extra={"user_id": user_id, "reason": fail_reason})
        return None

    mood = normalize_mood(parsed.get("mood"))
    if mood is None:
        logger.info("mood_update: invalid mood", extra={"user_id": user_id})
        return None

    await emit_companion_mood(user_id, mood)
    logger.info("mood_update: emitted", extra={"user_id": user_id})
    return mood
