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
from .affect_emit import emit_companion_affect
from .prompt_runtime import load_companion_prompt_context, run_prompt_json

logger = get_logger(__name__)


class AffectCheckResult(BaseModel):
    expressed: bool
    emotion: str = "neutral"
    actions: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(default="", max_length=200)


_MAX_RESPONSE_TOKENS = 340

_AFFECT_CHECK_PROMPT_TEMPLATE = (
    "你是桌面伙伴的视觉表达推理引擎。基于以下信息判断此刻是否应该流露情绪或播放动作。\n"
    "「表达」只是桌面精灵的情绪与动画变化，不是发消息、不是说话。\n"
    "你的角色定义：\n{persona_extras}\n\n"
    "你对用户的长期记忆：\n{memories_block}\n\n"
    "最近的对话：\n{recent_context}\n\n"
    "当前情境：\n"
    "- 用户已离开（无键鼠活动）{idle_minutes} 分钟\n"
    "- 用户本地时间：{local_hour} 点\n\n"
    "判断原则：\n"
    "- 如果角色性格 + 情境确实值得一个自然的视觉流露（如粘人型被冷落很久 → lonely/委屈；"
    "深夜 → sleepy；用户刚离开不久 → 多数情况无需表达），返回 should_express=true，并选择 emotion 和/或 actions\n"
    "- 如果没什么值得表达的、或情境不合适（如用户刚离开 5 分钟、或正在专注工作），"
    "返回 should_express=false\n"
    "- 情绪应该是角色个性的自然流露，不是机械的规则触发\n"
    "- actions 是按播放顺序排列的具体动画，最多 3 个，只能从当前可用清单精确选择；没有合适动作就返回空数组\n"
    "- 不要过度表达——沉默也是一种陪伴，大部分检查应该返回 false\n\n"
    "只返回 JSON，不要有任何其他文字：\n"
    '{{"should_express": true/false, "emotion": "EMOTION", "actions": ["ACTION"]}}\n\n'
    "emotion 必须是以下之一（如果 should_express=false，填 neutral）："
    " {allowed_emotions}\n"
    "actions 当前可用清单：{available_actions}"
)


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
            "available_actions": ", ".join(ctx.available_actions) or "无（必须返回空数组）",
        },
        max_output_tokens=_MAX_RESPONSE_TOKENS,
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
