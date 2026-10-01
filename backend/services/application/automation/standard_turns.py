import asyncio
from weakref import WeakValueDictionary

from components import get_logger, session_scope
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import emit_ws_event

from services.application.chat import HeadlessEmitter, run_chat_turn
from services.domains.automation import resolve_job_conversation
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)
# 锁在仍有触发持有或等待时存活，全部结束后随引用释放移除，任务删除后不会累积。
_STANDARD_TURN_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


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
) -> None:
    """同一任务串行执行，避免短周期任务把同一历史交错写入。"""
    lock = _STANDARD_TURN_LOCKS.setdefault(job_id, asyncio.Lock())
    async with lock:
        await _execute_standard_turn(user_id, job_id, name, prompt, conversation_id)
