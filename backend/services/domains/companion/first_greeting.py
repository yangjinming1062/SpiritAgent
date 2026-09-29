"""初次问候：引导首次完成时保存一条主动回合意图；是否开口与台词由伙伴在回合中决定。"""

import asyncio
import json
from datetime import timedelta

from components import (
    TaskBag,
    begin_user_request,
    end_user_request,
    get_logger,
    resolve_prompt_text,
    track_user_task,
    utc_now,
)
from modules.settings import UserSetting
from prompts.companion import FIRST_MEETING_INTENT_TEXTS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .intents import enqueue_companion_intent, queue_companion_intent

logger = get_logger(__name__)

_FIRST_GREETING_SOURCE_KEY = "onboarding:first_greeting"
# 有效期内仍须通过在线、档位与冷却闸门；逾期静默作废，不补发。
_FIRST_GREETING_TTL = timedelta(hours=2)

_BG = TaskBag("companion.first_greeting")


async def enqueue_first_greeting(db: AsyncSession, user_id: int) -> None:
    """在完成引导的事务中保存意图；说明文本与该用户的界面语言一致，由调用方提交。"""
    language = (
        await db.execute(
            select(UserSetting.setting_value).where(
                UserSetting.user_id == user_id,
                UserSetting.setting_key == "language",
            ),
        )
    ).scalar()
    intent = json.dumps(
        {"kind": "first_meeting", "intent": resolve_prompt_text(FIRST_MEETING_INTENT_TEXTS, language)},
        ensure_ascii=False,
    )
    await enqueue_companion_intent(
        db,
        user_id,
        intent,
        source_key=_FIRST_GREETING_SOURCE_KEY,
        expires_at=utc_now() + _FIRST_GREETING_TTL,
    )


def schedule_first_greeting_claim(user_id: int) -> None:
    """提交后在后台尝试认领；桌面尚未上报可用时，由可用性信号与等待扫描稍后认领。"""
    task = asyncio.create_task(_claim(user_id), name=f"companion.first_greeting.{user_id}")
    _BG.add(task, on_error=_log_claim_error)
    track_user_task(user_id, task)


async def _claim(user_id: int) -> None:
    if not await begin_user_request(user_id):
        return
    try:
        await queue_companion_intent(user_id)
    finally:
        await end_user_request(user_id)


def _log_claim_error(task: asyncio.Task) -> None:
    logger.error("first greeting claim failed", exc_info=task.exception())


async def drain() -> None:
    """取消并等待在途认领，由进程停机统一调用。"""
    await _BG.drain()
