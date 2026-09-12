import asyncio

from components import get_logger, session_scope
from modules.system import ChatMessageRequest, ChatRequest

from services.application.chat import HeadlessEmitter, run_chat_turn
from services.domains.companion import emit_companion_message
from services.domains.companion.disturbance import get_disturbance_tier
from services.domains.conversation import get_or_create_special_conversation
from services.infrastructure.desktop import MANAGER
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)

_SPECIAL_TURN_LOCKS: dict[int, asyncio.Lock] = {}
_SILENT_OUTPUT = "<silent>"


def _build_proactive_hint(prompt: str, disturbance_tier: str) -> str:
    return (
        "[INTERNAL PROACTIVE TRIGGER — this is an in-memory hint, not a user message.]\n"
        f"Scheduled intent: {prompt}\n"
        f"Effective disturbance tier: {disturbance_tier}.\n"
        "First perform a silent internal assessment. Use system sensing tools when useful to check "
        "idle time, lock state, focused application, and fullscreen state; also consider the current "
        "local time and the real conversation history above. Do not narrate this assessment. "
        "If speaking would be intrusive, unnecessary, or insincere, output exactly <silent>. "
        "Otherwise output only the natural words you want to say directly to the user. "
        "Never call send_message_tool: your final text is delivered automatically."
    )


async def execute_cron_turn(user_id: int, payload: dict) -> None:
    """在伴侣主会话上下文上执行无头主动回合，终态正文由后端原子晋级。"""
    if MANAGER.get_dispatcher(user_id) is None:
        logger.debug("special cron turn claimed but user disconnected", extra={"user_id": user_id})
        return

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return

    async with _SPECIAL_TURN_LOCKS.setdefault(user_id, asyncio.Lock()):
        disturbance_tier = await get_disturbance_tier(user_id)
        if disturbance_tier == "still":
            return

        async with session_scope() as db:
            conversation = await get_or_create_special_conversation(db, user_id, "companion")
            llm_config = await resolve_user_llm_config(db, user_id)

        request = ChatRequest(
            session_id=str(conversation.id),
            message=ChatMessageRequest(
                role="user",
                content=_build_proactive_hint(prompt, disturbance_tier),
            ),
        )
        emitter = HeadlessEmitter()
        try:
            await run_chat_turn(
                request,
                llm_config,
                user_id,
                emitter,
                ephemeral=True,
                headless=True,
                excluded_tool_names=frozenset({"send_message_tool"}),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("special cron turn failed", extra={"user_id": user_id, "job_id": payload.get("job_id")})
            return

        text = emitter.final_text.strip()
        if not text or text.casefold() == _SILENT_OUTPUT:
            return
        if await get_disturbance_tier(user_id) == "still" or MANAGER.get_dispatcher(user_id) is None:
            return

        try:
            await emit_companion_message(user_id, text)
        except Exception:
            logger.exception(
                "special cron delivery failed",
                extra={"user_id": user_id, "job_id": payload.get("job_id")},
            )
