import time
from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
)
from prompts.companion import SHOULD_ACT_INSTRUCTIONS
from pydantic import BaseModel, Field

from services.domains.conversation import load_recent_context_window
from services.infrastructure.llm import UserLlmConfig

from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)

ALLOWED_ACTIONS: frozenset[str] = frozenset({"roam", "perch", "approach", "stay"})

# approach 专属低频闸：一次搭话=走位+主动消息+TTS，频繁会变骚扰；冷却内 approach 整体降级为 stay（不发消息也不返回 approach），避免「说了话却没走位」的割裂——客户端只认 action 走位。
_last_approach_at: dict[int, float] = {}


def invalidate_user_should_act(user_id: int) -> None:
    _last_approach_at.pop(user_id, None)


# 超长开场白不交付，避免裁切改变伙伴表达。
_MAX_APPROACH_TEXT_CHARS = 80


class ShouldActResult(BaseModel):
    should_act: bool = False
    action: str | None = None
    params: dict[str, Any] | None = None
    reason: str = Field(default="", max_length=200)


def _normalize_approach_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    """approach 的 params 收敛为必填短文本；返回 None = 没有可用开场白，调用方降级 stay——没话可说的搭话只剩走位，失去意义。"""
    raw_text = params.get("text") if isinstance(params, dict) else None
    text = raw_text.strip() if isinstance(raw_text, str) else ""
    if not text or len(text) > _MAX_APPROACH_TEXT_CHARS:
        return None
    return {"text": text}


async def should_act(
    user_id: int,
    *,
    idle_seconds: float,
    local_hour: int,
    focused_category: str | None,
    fullscreen: bool,
    screen_locked: bool,
    seconds_since_last_action: float,
    llm_config: UserLlmConfig,
) -> ShouldActResult:
    """由 LLM 决策伙伴此刻是否要采取自主空间行为；数值参数由调用方归一化（local_hour 为 -1 表示未知）。"""
    if screen_locked or fullscreen:
        return ShouldActResult(should_act=False, action="stay", reason="screen_unavailable")

    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return ShouldActResult(should_act=False, reason="persona not ready")

    # 搭话台词直接进入主会话；近期对话让开场白不重复、不违背用户刚表达的意愿。锁屏与全屏已在上方返回，不再作为资料。
    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or ""

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        resolve_prompt_text(SHOULD_ACT_INSTRUCTIONS, ctx.language),
        {
            "output_language": ctx.language,
            "persona": ctx.persona_extras,
            "idle_minutes": round(idle_seconds / 60, 2),
            "local_hour": local_hour if local_hour >= 0 else None,
            "last_action_seconds": round(seconds_since_last_action, 1),
            **({"focused_category": focused_category} if focused_category else {}),
            **({"long_term_memories": ctx.memories_block} if ctx.memories_block else {}),
            **({"recent_context": recent_context} if recent_context else {}),
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="should_act",
    )
    if parsed is None:
        return ShouldActResult(should_act=False, reason=fail_reason or "llm_error")

    should_act_bool = parsed.get("should_act") is True
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
            logger.info("should_act: approach downgraded (invalid text)", extra={"user_id": user_id, "reason": reason})
            return ShouldActResult(should_act=False, action="stay", reason=(f"approach_invalid_text: {reason}")[:200])
        _last_approach_at[user_id] = now
        params = normalized
    else:
        params = {}

    logger.info("should_act: decided to act", extra={"user_id": user_id, "action": action, "reason": reason})
    return ShouldActResult(should_act=True, action=action, params=params, reason=reason)
