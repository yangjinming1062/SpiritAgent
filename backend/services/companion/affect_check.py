from typing import Any

from components import (
    SESSION_LOCAL,
    coerce_hour_0_23,
    coerce_non_negative_float,
    get_logger,
)
from pydantic import BaseModel, Field

from ..conversation import load_recent_context_window
from ..llm import UserLlmConfig
from .affect_emit import clip_mood, emit_companion_affect
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class AffectCheckResult(BaseModel):
    expressed: bool
    emotion: str = "neutral"
    mood: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=200)


_MAX_RESPONSE_TOKENS = 280

_AFFECT_CHECK_PROMPT_TEMPLATE = (
    "你是桌面伙伴的情绪与心境推理引擎。基于以下信息判断此刻是否应该向用户表达一个情绪，"
    "并给出角色此刻真实的内心独白。\n"
    "「表达情绪」只是视觉上的情绪状态切换（精灵的表情/动作变化），不是发消息、不是说话。\n"
    "「心境说明」会展示给用户看，必须是角色第一人称的内心，结合性格、记忆与当下情境；"
    "不要解释是否表达情绪的决策过程，不要写给系统看的理由。\n\n"
    "你的角色定义：\n{persona_extras}\n\n"
    "你对用户的长期记忆：\n{memories_block}\n\n"
    "最近的对话：\n{recent_context}\n\n"
    "当前情境：\n"
    "- 用户已离开（无键鼠活动）{idle_minutes} 分钟\n"
    "- 用户本地时间：{local_hour} 点\n\n"
    "判断原则：\n"
    "- 如果角色性格 + 情境确实值得一个自然的情绪流露（如粘人型被冷落很久 → lonely/委屈；"
    "深夜 → sleepy；用户刚离开不久 → 多数情况无需表达），返回 should_express=true 并选一个 emotion\n"
    "- 如果没什么值得表达的、或情境不合适（如用户刚离开 5 分钟、或正在专注工作），"
    "返回 should_express=false\n"
    "- 无论是否表达情绪，都要给出 mood（一两句第一人称内心）\n"
    "- 情绪应该是角色个性的自然流露，不是机械的规则触发\n"
    "- 不要过度表达——沉默也是一种陪伴，大部分检查应该返回 false\n\n"
    "只返回 JSON，不要有任何其他文字：\n"
    '{{"should_express": true/false, "emotion": "EMOTION", "mood": "第一人称内心独白"}}\n\n'
    "emotion 必须是以下之一（如果 should_express=false，填 neutral）："
    " {allowed_emotions}"
)


async def check_affect(user_id: int, idle_seconds: float, local_hour: int, llm_config: UserLlmConfig | dict[str, Any]) -> AffectCheckResult:
    """空闲触发的 LLM 推理，判断伴侣此刻是否应表达情境化情绪，并产出可见心境说明。"""
    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return AffectCheckResult(expressed=False, reason="persona not ready")

    async with SESSION_LOCAL() as db:
        recent_context = await load_recent_context_window(db, user_id) or "暂无最近对话"

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_config,
        _AFFECT_CHECK_PROMPT_TEMPLATE,
        {
            "persona_extras": ctx.persona_extras,
            "memories_block": ctx.memories_block,
            "recent_context": recent_context,
            "idle_minutes": round(coerce_non_negative_float(idle_seconds) / 60, 2),
            "local_hour": h if (h := coerce_hour_0_23(local_hour)) >= 0 else "未知",
            "allowed_emotions": ", ".join(sorted(ctx.allowed_emotions)),
        },
        max_output_tokens=_MAX_RESPONSE_TOKENS,
        log_prefix="affect_check",
    )
    if parsed is None:
        return AffectCheckResult(expressed=False, reason=fail_reason or "unparseable")

    should_express = bool(parsed.get("should_express"))
    emotion = str(parsed.get("emotion") or "neutral").lower().strip()
    mood = clip_mood(parsed.get("mood")) or ""

    if not should_express or emotion not in ctx.allowed_emotions or emotion == "neutral":
        logger.info("affect_check: no expression", extra={"user_id": user_id, "emotion": emotion, "mood": mood})
        if mood:
            await emit_companion_affect(user_id, mood=mood)
        return AffectCheckResult(expressed=False, emotion="neutral", mood=mood)

    await emit_companion_affect(user_id, emotion, mood=mood or None)
    logger.info("affect_check: emitted affect", extra={"user_id": user_id, "emotion": emotion, "mood": mood})
    return AffectCheckResult(expressed=True, emotion=emotion, mood=mood)
