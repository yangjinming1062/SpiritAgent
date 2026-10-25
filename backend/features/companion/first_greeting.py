"""初次问候：引导首次完成时保存一条主动回合意图；是否开口与台词由伙伴在回合中决定。"""

import asyncio
from datetime import timedelta

from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text, utc_now
from runtime.background import TaskBag
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.intents import claim_companion_intent, enqueue_companion_intent
from features.companion.prompts import FIRST_MEETING_INTENT_TEXTS
from features.configuration.settings.values import get_user_setting

logger = get_logger(__name__)

# 有效期内仍须通过在线、档位与冷却闸门；逾期静默作废，不补发。
_FIRST_GREETING_TTL = timedelta(hours=2)

_BG = TaskBag("companion.first_greeting")


async def enqueue_first_greeting(db: AsyncSession, user_id: int) -> None:
    """在完成引导的事务中保存意图；说明文本与该用户的界面语言一致，由调用方提交。"""
    language = await get_user_setting(db, user_id, "language")
    await enqueue_companion_intent(
        db,
        user_id,
        resolve_prompt_text(FIRST_MEETING_INTENT_TEXTS, language),
        source_kind="first_greeting",
        expires_at=utc_now() + _FIRST_GREETING_TTL,
    )


def schedule_first_greeting_claim(user_id: int) -> None:
    """提交后在后台尝试认领；桌面尚未上报可用时，由可用性信号与等待扫描稍后认领。"""
    task = asyncio.create_task(claim_companion_intent(user_id), name=f"companion.first_greeting.{user_id}")
    _BG.add(task, user_id=user_id, on_error=_log_claim_error)


def _log_claim_error(task: asyncio.Task) -> None:
    logger.error("first greeting claim failed", exc_info=task.exception())


async def drain() -> None:
    """取消并等待在途认领，由进程停机统一调用。"""
    await _BG.drain()
