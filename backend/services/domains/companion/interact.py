from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
    resolve_prompt_text,
)
from prompts.companion import INTERACT_INSTRUCTIONS
from pydantic import BaseModel, Field

from services.domains.conversation import load_recent_context_window
from services.infrastructure.llm import UserLlmConfig

from .interaction_stats import read_today_summary
from .mood import emit_companion_mood, normalize_mood
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class InteractResult(BaseModel):
    text: str | None = None
    emotion: str | None = None
    mood: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=200)


REGION_NAMES_ZH: dict[str, str] = {
    "head": "头部",
    "face": "脸部",
    "arm_L": "左手臂",
    "arm_R": "右手臂",
    "body": "身体",
    "back_hair": "后发",
    "front_hair": "前发刘海",
    "skirt": "裙摆",
}


async def interact(
    user_id: int,
    kind: str,
    poke_count: int,
    idle_seconds: float,
    local_hour: int,
    llm_config: UserLlmConfig | dict[str, Any],
    region: str | None = None,
) -> InteractResult:
    """针对用户互动（戳一戳、摸头抚摸、眩晕）用 LLM 生成口头反应与表情。"""
    if kind not in ("poke", "pet", "dizzy"):
        return InteractResult(text=None, reason="invalid_kind")

    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return InteractResult(text=None, reason="persona not ready")

    today = await read_today_summary(user_id)
    today_stats = today["content"] if today else ""

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    idle_minutes = round(coerce_non_negative_float(idle_seconds) / 60, 2)
    local_hour = coerce_hour_0_23(local_hour)
    if kind == "pet":
        action_desc = "温柔地抚摸了你的头顶，揉了揉你"
    elif kind == "dizzy":
        action_desc = "连续快速戳你或者高速晃动你，让你有些头晕目眩"
    else:
        action_desc = f"戳了戳你的{REGION_NAMES_ZH[region]}" if region in REGION_NAMES_ZH else "戳了戳你"

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(INTERACT_INSTRUCTIONS, ctx.language),
        {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "interaction": action_desc,
            "interaction_intensity_bucket": poke_count,
            "local_hour": local_hour if local_hour >= 0 else None,
            "idle_minutes": idle_minutes,
            "allowed_emotions": sorted(ctx.allowed_emotions),
            **({"current_outfit": ctx.outfit_block} if ctx.outfit_block else {}),
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
            **({"recent_context": recent_context} if recent_context else {}),
            **({"today_interaction_summary": today_stats} if today_stats else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="interact",
        temperature=0.7,
    )
    if parsed is None:
        return InteractResult(text=None, reason=fail_reason or "llm_error")

    text = str(parsed.get("text") or "").strip()
    if not text:
        return InteractResult(text=None, reason="unparseable")
    if len(text) > 40:
        text = text[:40]

    raw_emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    emotion = raw_emotion if raw_emotion in ctx.allowed_emotions and raw_emotion != "neutral" else None

    mood = normalize_mood(parsed.get("mood")) or ""
    if mood:
        await emit_companion_mood(user_id, mood)

    logger.info(
        "interact: generated interaction response",
        extra={
            "user_id": user_id,
            "kind": kind,
            "region": region,
            "text": text,
            "emotion": emotion,
            "mood": mood,
        },
    )
    return InteractResult(text=text, emotion=emotion, mood=mood, reason="ok")
