import asyncio
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

from components import get_logger, session_scope
from modules.auth import User
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import emit_ws_event
from sqlalchemy import select

from services.application.chat import HeadlessEmitter, run_chat_turn
from services.domains.automation import STANDARD_CRON_KIND, get_user_job, resolve_job_conversation
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)

# 同一任务已受理且未结束的触发上限：持锁运行的一个和等待锁的一个。
_MAX_ADMITTED_TURNS = 2


@dataclass
class _TurnSlot:
    """同一任务的执行槽：admitted 统计已受理且未结束的触发，上限 ``_MAX_ADMITTED_TURNS``。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    admitted: int = 0


# 槽在仍有触发持有或等待时存活，全部结束后随引用释放移除，任务删除后不会累积。
_STANDARD_TURN_SLOTS: WeakValueDictionary[int, _TurnSlot] = WeakValueDictionary()


async def _emit_notification(user_id: int, *, name: str, text: str, conversation_id: int, error: bool = False) -> None:
    preview = text.strip()
    if len(preview) > 240:
        preview = preview[:237].rstrip() + "..."
    async with session_scope() as db:
        emit_ws_event(
            db,
            user_id=user_id,
            event_type="system.notification",
            payload={
                "kind": "error" if error else "success",
                "title": name,
                "message": preview or ("任务执行失败" if error else "任务已完成"),
                "session_id": str(conversation_id),
            },
        )
        await db.commit()


async def _execute_standard_turn(
    user_id: int,
    job_id: int,
    name: str,
    prompt: str,
    conversation_id: int | None,
) -> None:
    prompt = prompt.strip()
    if not prompt:
        return
    name = name.strip() or "定时任务"

    try:
        conversation_id = await resolve_job_conversation(user_id, job_id, conversation_id, name)
        async with session_scope() as db:
            llm_config = await resolve_user_llm_config(db, user_id)
        emitter = HeadlessEmitter()
        request = ChatRequest(
            session_id=str(conversation_id),
            message=ChatMessageRequest(content=prompt),
        )
        await run_chat_turn(request, llm_config, user_id, emitter, headless=True)
        if emitter.error:
            await _emit_notification(
                user_id,
                name=name,
                text="任务执行失败，请稍后重试",
                conversation_id=conversation_id,
                error=True,
            )
            return
        await _emit_notification(user_id, name=name, text=emitter.final_text, conversation_id=conversation_id)
    except Exception:
        logger.exception("standard cron turn failed", extra={"user_id": user_id, "job_id": job_id})
        if conversation_id is not None:
            await _emit_notification(
                user_id,
                name=name,
                text="任务执行失败，请稍后重试",
                conversation_id=conversation_id,
                error=True,
            )


async def execute_standard_turn(
    user_id: int,
    job_id: int,
    *,
    name: str,
    prompt: str,
    conversation_id: int | None,
    one_shot: bool,
) -> None:
    """同一任务串行执行，避免短周期任务把同一历史交错写入；至多一个运行中和一个待执行触发，其余合并丢弃。待执行触发获得执行权后重读任务：已删除、暂停或改为 special 的丢弃，否则按任务当前内容运行；一次性任务触发时已删除，沿用触发时的内容。"""
    slot = _STANDARD_TURN_SLOTS.setdefault(job_id, _TurnSlot())
    if slot.admitted >= _MAX_ADMITTED_TURNS:
        logger.info("standard cron trigger coalesced", extra={"user_id": user_id, "job_id": job_id})
        return
    slot.admitted += 1
    try:
        async with slot.lock:
            async with session_scope() as db:
                active = await db.scalar(select(User.is_active).where(User.id == user_id))
            if not active:
                logger.info(
                    "standard cron trigger dropped: user inactive",
                    extra={"user_id": user_id, "job_id": job_id},
                )
                return
            if not one_shot:
                current = await get_user_job(user_id, job_id)
                if current is None or current["is_paused"] or current["kind"] != STANDARD_CRON_KIND:
                    logger.info(
                        "standard cron trigger dropped: job removed, paused or no longer standard",
                        extra={"user_id": user_id, "job_id": job_id},
                    )
                    return
                name, prompt, conversation_id = current["name"], current["prompt"], current["conversation_id"]
            await _execute_standard_turn(user_id, job_id, name, prompt, conversation_id)
    finally:
        slot.admitted -= 1
