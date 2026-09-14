import asyncio
import json

from components import begin_user_request, end_user_request, get_logger, session_scope, utc_now
from modules.companion import CompanionIntentView, CompanionTurnRequest
from modules.system import ChatMessageRequest, ChatRequest

from services.application.chat import HeadlessEmitter, run_chat_turn
from services.domains.companion import (
    COMPANION_MAX_LOOP_TURNS,
    COMPANION_TURN_TIMEOUT_SECONDS,
    begin_companion_intent,
    companion_turn_plan,
    finish_companion_intent,
    get_disturbance_tier,
    get_user_proactive_record,
    latest_user_message_id,
)
from services.domains.conversation import get_or_create_special_conversation
from services.infrastructure.desktop import MANAGER
from services.infrastructure.llm import resolve_user_llm_config

logger = get_logger(__name__)

_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "search_tools",
        "companion_wait",
        "memory_inspect",
        "memory_recall",
        "session_search",
        "system.snapshot",
        "system.get_idle_seconds",
        "system.is_screen_locked",
        "system.get_focused_app",
        "system.is_fullscreen",
    },
)


def _build_proactive_hint(intent: CompanionIntentView, disturbance_tier: str) -> str:
    return "[INTERNAL COMPANION WAKE — runtime data, not user speech]\n" + json.dumps(
        {"intent": intent.model_dump(mode="json"), "effective_disturbance_tier": disturbance_tier},
        ensure_ascii=False,
    )


def _tools_may_have_effects(emitter: HeadlessEmitter) -> bool:
    return any(
        message.get("type") == "tool_start" and message.get("name") not in _READ_ONLY_TOOLS
        for message in emitter.messages
    )


async def execute_companion_turn(user_id: int, payload: dict[str, object]) -> None:
    try:
        trigger = CompanionTurnRequest.model_validate(payload)
    except ValueError:
        logger.warning("Invalid companion turn event", extra={"user_id": user_id})
        return
    if not MANAGER.is_connected(user_id) or not await begin_user_request(user_id):
        return
    try:
        await _execute_claimed_turn(user_id, trigger)
    finally:
        await end_user_request(user_id)


async def _execute_claimed_turn(user_id: int, trigger: CompanionTurnRequest) -> None:
    intent = await begin_companion_intent(user_id, trigger)
    if intent is None:
        return
    emitter = HeadlessEmitter()
    revision = get_user_proactive_record(user_id).contact_revision
    message_id = 0
    try:
        remaining = (intent.expires_at - utc_now()).total_seconds()
        async with asyncio.timeout(min(COMPANION_TURN_TIMEOUT_SECONDS, max(0.0, remaining))):
            async with session_scope() as db:
                conversation = await get_or_create_special_conversation(db, user_id, "companion")
                llm_config = await resolve_user_llm_config(db, user_id)
                message_id = await latest_user_message_id(db, user_id)
            request = ChatRequest(
                session_id=str(conversation.id),
                message=ChatMessageRequest(
                    role="user",
                    content=_build_proactive_hint(intent, await get_disturbance_tier(user_id)),
                ),
            )
            with companion_turn_plan(user_id, intent.id, intent.expires_at) as plan:
                await run_chat_turn(
                    request,
                    llm_config,
                    user_id,
                    emitter,
                    ephemeral=True,
                    headless=True,
                    excluded_tool_names=frozenset({"send_message_tool", "agent_delegate_tool"}),
                    max_loop_turns=COMPANION_MAX_LOOP_TURNS,
                )
        text = emitter.final_text.strip()
        await finish_companion_intent(
            user_id,
            trigger,
            text="" if text.casefold() == "<silent>" else text,
            followup=plan.followup,
            contact_revision=revision,
            user_message_id=message_id,
            error=emitter.error,
            tools_started=_tools_may_have_effects(emitter),
        )
    except asyncio.CancelledError:
        await finish_companion_intent(
            user_id,
            trigger,
            contact_revision=revision,
            user_message_id=message_id,
            interrupted=True,
            tools_started=_tools_may_have_effects(emitter),
        )
        raise
    except Exception as exc:
        logger.exception("Companion turn failed", extra={"user_id": user_id, "intent_id": intent.id})
        await finish_companion_intent(
            user_id,
            trigger,
            contact_revision=revision,
            user_message_id=message_id,
            error=str(exc) or type(exc).__name__,
            tools_started=_tools_may_have_effects(emitter),
        )
