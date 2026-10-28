"""陪伴回合后的即时互动反应：从真实对话推断一次身体反应演出，供导演消费。

反应不是台词：不落消息正文、不改生活事实，只作为桌面端的即时演出建议；
播放由客户端按可用素材、优先级与动态表现设置决定。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text, utc_now
from runtime.events.emit import emit_ws_event

from features.companion.prompt_runtime import load_companion_prompt_context, run_prompt_json
from features.companion.prompts import REACTION_INSTRUCTIONS
from features.conversation.context_window import load_recent_context_window

logger = get_logger(__name__)


# 有限语义集合：客户端按 preset_key 匹配可用反应动作；空串表示本回合不演。
class ReactionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reaction: Literal["", "look", "smile", "stretch"]


async def emit_companion_reaction(user_id: int, user_message: str, assistant_bubbles: list[str]) -> str | None:
    """陪伴聊天完成后推断一次身体反应并广播；失败静默跳过，不影响消息交付。"""

    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return None

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    remaining = 2000
    recent_bubbles: list[str] = []
    for text in reversed(assistant_bubbles):
        if remaining <= 0:
            break
        recent_bubbles.append(text[-remaining:])
        remaining -= len(text)
    recent_bubbles.reverse()
    parsed, fail_reason = await run_prompt_json(
        user_id,
        await load_user_llm_config(user_id, AiTaskKey.companion_chat),
        resolve_prompt_text(REACTION_INSTRUCTIONS, ctx.language),
        {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "user_message": user_message[-2000:],
            "assistant_bubbles": recent_bubbles,
            **({"recent_context": recent_context} if recent_context else {}),
        },
        log_prefix="companion_reaction",
        temperature=0.3,
    )
    if parsed is None:
        logger.info("companion_reaction: skipped", extra={"user_id": user_id, "reason": fail_reason})
        return None

    try:
        reaction = ReactionDecision.model_validate(parsed).reaction
    except ValidationError:
        logger.info("companion_reaction: invalid choice", extra={"user_id": user_id})
        return None
    if not reaction:
        return None
    async with SESSION_LOCAL() as db:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="companion.reaction",
            payload={"reaction": reaction, "occurred_at": utc_now().isoformat()},
        )
        await db.commit()
    return reaction
