"""白天自主片刻冲动：调度器低频咨询 LLM，由精灵结合心境、记忆与近期互动决定是否发一条片刻。

与聊天内 ``moment_create`` 工具和夜间规划并列的第三条精灵发起通道；只写文本片刻
（图片/视频心意仍归夜间），不发主对话消息、不做桌面打扰。静止档断源（ARCHITECTURE §5.1）。
"""

import random
import time
from typing import Any

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    get_logger,
    is_user_in_maintenance,
    resolve_prompt_text,
    utc_now,
)
from modules.companion import CompanionMoment, MomentKind, MomentSource
from prompts.nightly import MOMENT_IMPULSE_INSTRUCTIONS
from sqlalchemy import select

from services.domains.companion import (
    get_disturbance_tier,
    load_companion_prompt_context,
    run_prompt_json,
)
from services.domains.conversation import load_recent_context_window
from services.domains.journal import check_moment_autonomous_quota, create_user_moment
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)

# 进程内 per-user 节流：到点才咨询一次 LLM，咨询间隔带随机抖动，避免节奏可预测。
_IMPULSE_MIN_INTERVAL_SECONDS = 45 * 60
_IMPULSE_MAX_INTERVAL_SECONDS = 90 * 60
_NEXT_CHECK_AT: dict[int, float] = {}

_RECENT_MOMENT_LIMIT = 10


async def maybe_run_moment_impulse(user_id: int) -> None:
    """调度器 tick 调用：到点后做一次廉价门控，通过才咨询 LLM 并可能写入片刻。"""
    now = time.monotonic()
    due = _NEXT_CHECK_AT.get(user_id)
    if due is None:
        # 首次见到该用户：先随机推迟一个完整周期，避免进程启动即集中咨询。
        _NEXT_CHECK_AT[user_id] = now + random.uniform(
            _IMPULSE_MIN_INTERVAL_SECONDS,
            _IMPULSE_MAX_INTERVAL_SECONDS,
        )
        return
    if now < due:
        return
    # 到点后先推进下一次窗口再执行：LLM 失败也等到下一窗口，避免重入风暴。
    _NEXT_CHECK_AT[user_id] = now + random.uniform(
        _IMPULSE_MIN_INTERVAL_SECONDS,
        _IMPULSE_MAX_INTERVAL_SECONDS,
    )
    if is_user_in_maintenance(user_id):
        return
    if await get_disturbance_tier(user_id) == "still":
        return

    ctx = await load_companion_prompt_context(user_id)
    if ctx is None:
        return

    async with SESSION_LOCAL() as db:
        if not await check_moment_autonomous_quota(db, user_id):
            return
        recent = (
            await db.execute(
                select(CompanionMoment.title, CompanionMoment.body)
                .where(CompanionMoment.user_id == user_id)
                .order_by(CompanionMoment.occurred_at.desc())
                .limit(_RECENT_MOMENT_LIMIT),
            )
        ).all()
        recent_context = await load_recent_context_window(db, user_id) or ""
        llm_cfg = await resolve_user_llm_config(db, user_id)
    if not llm_cfg.model_name:
        return

    payload: dict[str, Any] = {
        "current_time": utc_now().isoformat(),
        "output_language": ctx.language,
        "persona": ctx.persona_extras,
        "recent_moments": [{"title": t, "body": b} for t, b in recent],
    }
    if ctx.current_mood:
        payload["current_mood"] = ctx.current_mood
    if ctx.memories_block:
        payload["long_term_memories"] = ctx.memories_block
    if recent_context:
        payload["recent_conversation"] = recent_context

    parsed, fail_reason = await run_prompt_json(
        user_id,
        llm_cfg,
        resolve_prompt_text(MOMENT_IMPULSE_INSTRUCTIONS, ctx.language),
        payload,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="moment_impulse",
        temperature=0.7,
    )
    if parsed is None:
        logger.info("moment_impulse: skipped", extra={"user_id": user_id, "reason": fail_reason})
        return
    if parsed.get("post") is not True:
        return
    title, body, emotion = parsed.get("title"), parsed.get("body"), parsed.get("emotion")
    if (
        not isinstance(title, str)
        or not 1 <= len(title.strip()) <= 24
        or not isinstance(body, str)
        or not 1 <= len(body.strip()) <= 500
        or emotion not in ("happy", "curious", "calm", "miss", "thoughtful", "proud", "soft")
    ):
        logger.info("moment_impulse: invalid post fields", extra={"user_id": user_id})
        return

    async with SESSION_LOCAL() as db:
        row = await create_user_moment(
            db,
            user_id,
            title=title,
            body=body,
            emotion=emotion,
            kind=MomentKind.EMOTION.value,
            source=MomentSource.AUTONOMOUS.value,
        )
    logger.info(
        "moment_impulse: posted",
        extra={"user_id": user_id, "moment_id": row.id},
    )
