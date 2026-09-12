import asyncio

from components import get_logger, session_scope
from modules.conversation import Conversation
from modules.scheduler import CronJob
from modules.system import ChatMessageRequest, ChatRequest
from modules.ws import emit_ws_event
from sqlalchemy import select

from services.application.chat import AUTOMATION_EXCLUDED_TOOL_NAMES, AUTOMATION_PRESET, HeadlessEmitter, run_chat_turn
from services.domains.conversation import STANDARD_KIND
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)
_STANDARD_TURN_LOCKS: dict[int, asyncio.Lock] = {}


async def _resolve_conversation(user_id: int, job_id: int, conversation_id: int | None, name: str) -> Conversation:
    async with session_scope() as db:
        conversation = None
        if conversation_id is not None:
            conversation = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.user_id == user_id,
                        Conversation.kind == STANDARD_KIND,
                        Conversation.is_automation.is_(True),
                    ),
                )
            ).scalar_one_or_none()
        if conversation is None:
            conversation = Conversation(
                user_id=user_id,
                kind=STANDARD_KIND,
                title=f"定时任务 · {name}",
                is_automation=True,
                system_preset_id="automation",
            )
            db.add(conversation)
            await db.flush()
            job = (
                await db.execute(select(CronJob).where(CronJob.id == job_id, CronJob.user_id == user_id))
            ).scalar_one_or_none()
            if job is not None:
                job.conversation_id = conversation.id
            await db.commit()
            await db.refresh(conversation)
        return conversation


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


async def _execute_standard_turn(user_id: int, job_id: int, payload: dict) -> None:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return
    name = str(payload.get("name") or "定时任务").strip() or "定时任务"
    raw_conversation_id = payload.get("conversation_id")
    conversation_id = raw_conversation_id if isinstance(raw_conversation_id, int) else None

    try:
        conversation = await _resolve_conversation(user_id, job_id, conversation_id, name)
        conversation_id = conversation.id
        async with session_scope() as db:
            llm_config = await resolve_user_llm_config(db, user_id)
        emitter = HeadlessEmitter()
        request = ChatRequest(
            session_id=str(conversation.id),
            message=ChatMessageRequest(role="user", content=prompt),
        )
        await run_chat_turn(
            request,
            llm_config,
            user_id,
            emitter,
            headless=True,
            excluded_tool_names=AUTOMATION_EXCLUDED_TOOL_NAMES,
            preset_override=AUTOMATION_PRESET,
            run_post_turn_tasks=False,
        )
        if emitter.error:
            await _emit_notification(
                user_id,
                name=name,
                text="任务执行失败，请稍后重试",
                conversation_id=conversation.id,
                error=True,
            )
            return
        await _emit_notification(user_id, name=name, text=emitter.final_text, conversation_id=conversation.id)
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


async def execute_standard_turn(user_id: int, job_id: int, payload: dict) -> None:
    """同一任务串行执行，避免短周期任务把同一历史交错写入。"""
    async with _STANDARD_TURN_LOCKS.setdefault(job_id, asyncio.Lock()):
        await _execute_standard_turn(user_id, job_id, payload)
