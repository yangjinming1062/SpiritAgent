from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
    resolve_prompt_text,
)
from prompts.companion import AFFECT_CHECK_INSTRUCTIONS
from pydantic import BaseModel, Field

from services.domains.conversation import load_recent_context_window
from services.infrastructure.llm import UserLlmConfig

from .affect_emit import emit_companion_affect
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class AffectCheckResult(BaseModel):
    expressed: bool
    emotion: str = "neutral"
    actions: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(default="", max_length=200)


def _normalize_actions(raw: object, allowed: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    actions: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        action = item.lower().strip().replace("\\_", "_")
        if action in allowed and action not in actions:
            actions.append(action)
        if len(actions) == 3:
            break
    return actions


async def check_affect(
    user_id: int,
    idle_seconds: float,
    local_hour: int,
    llm_config: UserLlmConfig | dict[str, Any],
) -> AffectCheckResult:
    """空闲触发的 LLM 推理，判断桌面精灵此刻是否应播放情境化视觉表达。"""
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return AffectCheckResult(expressed=False, reason="persona not ready")

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(AFFECT_CHECK_INSTRUCTIONS, ctx.language),
        {
            "persona": ctx.persona_extras,
            "idle_minutes": round(coerce_non_negative_float(idle_seconds) / 60, 2),
            "local_hour": h if (h := coerce_hour_0_23(local_hour)) >= 0 else None,
            "allowed_emotions": sorted(ctx.allowed_emotions),
            "available_actions": ctx.available_actions,
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
            **({"recent_context": recent_context} if recent_context else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="affect_check",
    )
    if parsed is None:
        return AffectCheckResult(expressed=False, reason=fail_reason or "unparseable")

    should_express = bool(parsed.get("should_express"))
    raw_emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    emotion = raw_emotion if raw_emotion in ctx.allowed_emotions else "neutral"
    actions = _normalize_actions(parsed.get("actions"), set(ctx.available_actions))
    if not should_express or (emotion == "neutral" and not actions):
        logger.info("affect_check: no expression", extra={"user_id": user_id, "emotion": emotion, "actions": actions})
        return AffectCheckResult(expressed=False, emotion="neutral")

    await emit_companion_affect(user_id, None if emotion == "neutral" else emotion, actions=actions)
    logger.info("affect_check: emitted affect", extra={"user_id": user_id, "emotion": emotion, "actions": actions})
    return AffectCheckResult(expressed=True, emotion=emotion, actions=actions)
