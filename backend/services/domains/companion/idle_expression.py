"""空闲表达：LLM 低频决定是否用当前形象动作做一次自主表演。

只产出 action_id；统一播放由调用方经 request_playback 派发。
"""

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
    resolve_prompt_text,
)
from prompts.companion import IDLE_EXPRESSION_INSTRUCTIONS
from pydantic import BaseModel

from services.domains.conversation import load_recent_context_window
from services.infrastructure.llm import UserLlmConfig

from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class IdleExpressionResult(BaseModel):
    expressed: bool
    action_id: int | None = None
    reason: str = ""


async def check_idle_expression(
    user_id: int,
    idle_seconds: float,
    local_hour: int,
    llm_config: UserLlmConfig,
) -> IdleExpressionResult:
    """空闲触发的 LLM 推理；决定是否播一个当前形象已就绪动作。"""
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return IdleExpressionResult(expressed=False, reason="persona not ready")
    if not ctx.available_actions:
        return IdleExpressionResult(expressed=False, reason="no available actions")

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(IDLE_EXPRESSION_INSTRUCTIONS, ctx.language),
        {
            "persona": ctx.persona_extras,
            "idle_minutes": round(coerce_non_negative_float(idle_seconds) / 60, 2),
            "local_hour": h if (h := coerce_hour_0_23(local_hour)) >= 0 else None,
            "available_actions": ctx.available_actions,
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
            **({"recent_context": recent_context} if recent_context else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="idle_expression",
    )
    if parsed is None:
        return IdleExpressionResult(expressed=False, reason=fail_reason or "unparseable")

    if not bool(parsed.get("should_express")):
        logger.info("idle_expression: skip", extra={"user_id": user_id})
        return IdleExpressionResult(expressed=False)

    allowed_ids = {int(item["action_id"]) for item in ctx.available_actions}
    raw_id = parsed.get("action_id")
    try:
        action_id = int(raw_id)
    except (TypeError, ValueError):
        return IdleExpressionResult(expressed=False, reason="invalid action_id")
    if action_id not in allowed_ids:
        return IdleExpressionResult(expressed=False, reason="action_id not in available_actions")

    logger.info("idle_expression: selected", extra={"user_id": user_id, "action_id": action_id})
    return IdleExpressionResult(expressed=True, action_id=action_id)
