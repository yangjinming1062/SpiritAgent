import time
from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SETTINGS,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
    resolve_prompt_text,
)
from prompts.companion import SHOULD_ACT_INSTRUCTIONS
from pydantic import BaseModel, Field

from services.infrastructure.llm import UserLlmConfig

from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)

ALLOWED_ACTIONS: frozenset[str] = frozenset({"roam", "perch", "approach", "stay"})

# approach（走过去搭话）专属低频闸：一次搭话 = 一次走位 + 一条主动消息 + 一次 TTS，
# 频繁搭话会把"主动陪伴"变成骚扰。冷却内的 approach 决策整体降级为 stay——
# 不发消息也不返回 approach，避免"说了话却没走过来"的割裂（客户端只认 action 走位）。
_last_approach_at: dict[int, float] = {}


def invalidate_user_should_act(user_id: int) -> None:
    _last_approach_at.pop(user_id, None)


# approach 开场白的硬上限：prompt 要 10–30 字，这里兜住 LLM 超发的长文（按字符截断）。
_MAX_APPROACH_TEXT_CHARS = 80


class ShouldActResult(BaseModel):
    should_act: bool = False
    action: str | None = None
    params: dict[str, Any] | None = None
    reason: str = Field(default="", max_length=200)


def _normalize_approach_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    """approach 的 params 收敛为必填的短文本。

    返回 None = 没有可用的开场白，调用方降级 stay——没有话可说的搭话只剩走位，失去意义。
    """
    raw_text = params.get("text") if isinstance(params, dict) else None
    text = str(raw_text).strip()[:_MAX_APPROACH_TEXT_CHARS] if isinstance(raw_text, str) else ""
    if not text:
        return None
    return {"text": text}


async def should_act(
    user_id: int,
    kind: str = "periodic_provision",
    idle_seconds: float = 0.0,
    local_hour: int = 0,
    focused_category: str | None = None,
    fullscreen: bool = False,
    screen_locked: bool = False,
    seconds_since_last_action: float = 0.0,
    llm_config: UserLlmConfig | dict[str, Any] | None = None,
) -> ShouldActResult:
    """由 LLM 决策伙伴此刻是否要采取自主空间行为。"""
    if kind not in ("periodic_provision",):
        return ShouldActResult(should_act=False, reason="invalid_kind")

    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return ShouldActResult(should_act=False, reason="persona not ready")

    idle_minutes = round(coerce_non_negative_float(idle_seconds) / 60, 2)
    last_action_sec = round(coerce_non_negative_float(seconds_since_last_action), 1)
    local_hour = coerce_hour_0_23(local_hour)
    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(SHOULD_ACT_INSTRUCTIONS, ctx.language),
        {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "idle_minutes": idle_minutes,
            "local_hour": local_hour if local_hour >= 0 else None,
            "last_action_seconds": last_action_sec,
            "fullscreen": fullscreen,
            "screen_locked": screen_locked,
            **({"focused_category": focused_category} if focused_category else {}),
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="should_act",
    )
    if parsed is None:
        return ShouldActResult(should_act=False, reason=fail_reason or "llm_error")

    should_act_bool = bool(parsed.get("should_act"))
    action = str(parsed.get("action") or "stay").lower().strip()
    reason = str(parsed.get("reason") or "")[:200]
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else None

    if not should_act_bool or action not in ALLOWED_ACTIONS or action == "stay":
        logger.info("should_act: decided not to act", extra={"user_id": user_id, "reason": reason})
        return ShouldActResult(should_act=False, action="stay", reason=reason)

    if action == "approach":
        now = time.monotonic()
        last = _last_approach_at.get(user_id)
        if last is not None and now - last < SETTINGS.companion_approach_cooldown_seconds:
            logger.info(
                "should_act: approach throttled",
                extra={"user_id": user_id, "since_sec": round(now - last, 1)},
            )
            return ShouldActResult(should_act=False, action="stay", reason="approach_cooldown")
        normalized = _normalize_approach_params(params)
        if normalized is None:
            logger.info("should_act: approach downgraded (no text)", extra={"user_id": user_id, "reason": reason})
            return ShouldActResult(should_act=False, action="stay", reason=(f"approach_no_text: {reason}")[:200])
        _last_approach_at[user_id] = now
        params = normalized

    logger.info("should_act: decided to act", extra={"user_id": user_id, "action": action, "reason": reason})
    return ShouldActResult(should_act=True, action=action, params=params, reason=reason)
