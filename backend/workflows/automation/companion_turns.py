import asyncio
import json

from features.companion.disturbance import get_disturbance_tier
from features.companion.intent_contracts import CompanionIntentView, CompanionTurnRequest
from features.companion.intents import (
    DeferredReplySource,
    begin_companion_intent,
    companion_turn_plan,
    deferred_reply_source,
    finish_companion_intent,
)
from features.companion.proactive_runtime import get_user_proactive_record
from features.companion.prompts import PROACTIVE_CONTEXT_LABELS
from features.configuration.settings.values import get_user_setting
from features.conversation.evidence import latest_user_message_id
from features.conversation.reply_audio import discard_reply_audio, prepare_reply_audio
from features.conversation.request_contracts import ChatMessageRequest, ChatRequest
from features.conversation.turn_access import ensure_main_conversation
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from runtime.desktop.connection import MANAGER
from runtime.tools.tool_result_classification import tool_may_have_effects
from runtime.user_maintenance import user_request_scope

from workflows.chat.chat_emitter import HeadlessEmitter
from workflows.chat.orchestrator import run_chat_turn

logger = get_logger(__name__)

_FAILURE_REASON_MAX_CHARS = 200


def _build_proactive_hint(
    intent: CompanionIntentView, disturbance_tier: str, language: str, source: DeferredReplySource | None
) -> str:
    data = {"intent": intent.model_dump(mode="json"), "disturbance_tier": disturbance_tier}
    if source is not None:
        data["deferred_from_user_message"] = {"id": str(source.message_id), "content": source.content}
    return resolve_prompt_text(PROACTIVE_CONTEXT_LABELS, language) + "\n" + json.dumps(data, ensure_ascii=False)


def _failure_reason(exc: Exception, language: str) -> str:
    """持久化原因供用户与后续模型读取；诊断原因由调用方完整记录。"""
    if isinstance(exc, TimeoutError):
        return (
            "This contact timed out; check any work already started."
            if language == "en"
            else "本次联系超时，请核对已开始的操作。"
        )
    return (
        "This contact could not complete; check any work already started."
        if language == "en"
        else "本次联系未能完成，请核对已开始的操作。"
    )


def _turn_error(emitter: HeadlessEmitter, language: str) -> str | None:
    for frame in reversed(emitter.messages):
        if frame.get("type") == "error":
            message = frame.get("message")
            if isinstance(message, str) and message.strip():
                return redact_sensitive_text(message.strip())[:_FAILURE_REASON_MAX_CHARS]
            return _failure_reason(RuntimeError(), language)
    return None


def _tools_may_have_effects(emitter: HeadlessEmitter) -> bool:
    return any(
        message.get("type") == "tool_start" and tool_may_have_effects(message.get("name"))
        for message in emitter.messages
    )


async def execute_companion_turn(user_id: int, payload: dict[str, object]) -> None:
    try:
        trigger = CompanionTurnRequest.model_validate(payload)
    except ValueError:
        logger.warning("Invalid companion turn event", extra={"user_id": user_id})
        return
    if not (MANAGER.is_connected(user_id) or MANAGER.has_connected_remote(user_id)):
        return
    async with user_request_scope(user_id) as acquired:
        if acquired:
            await _execute_claimed_turn(user_id, trigger)


async def _execute_claimed_turn(user_id: int, trigger: CompanionTurnRequest) -> None:
    intent = await begin_companion_intent(user_id, trigger)
    if intent is None:
        return
    emitter = HeadlessEmitter()
    revision = get_user_proactive_record(user_id).contact_revision
    message_id = 0
    reply = None
    delivered = False
    language = "zh"
    source = None
    try:
        remaining = (intent.expires_at - utc_now()).total_seconds()
        async with asyncio.timeout(max(0.0, remaining)):
            async with session_scope() as db:
                if intent.source_kind and intent.source_kind.startswith("reply:"):
                    source = await deferred_reply_source(db, user_id, intent.source_kind)
                    conversation_id = source.conversation_id if source is not None else None
                else:
                    conversation_id = (await ensure_main_conversation(db, user_id)).id
                message_id = await latest_user_message_id(db, user_id)
                disturbance_tier = await get_disturbance_tier(user_id, db=db)
                language = resolve_language(await get_user_setting(db, user_id, "language"))
            if conversation_id is None:
                await finish_companion_intent(user_id, trigger, contact_revision=revision, user_message_id=message_id)
                return
            request = ChatRequest(
                session_id=str(conversation_id),
                message=ChatMessageRequest(content=_build_proactive_hint(intent, disturbance_tier, language, source)),
            )
            with companion_turn_plan(user_id, intent.id, intent.expires_at) as plan:
                await run_chat_turn(
                    request,
                    user_id,
                    emitter,
                    ephemeral=True,
                    headless=True,
                    # 常规档主动回合只提供文字能力：语音由回合装配按档位关闭，视觉表达在此排除。
                    excluded_tool_names=frozenset({"send_message_tool", "agent_delegate_tool"})
                    | (
                        frozenset()
                        if disturbance_tier == "autonomous"
                        else frozenset(
                            {"visual_play", "visual_generate", "image_generate", "image_regenerate", "video_generate"}
                        )
                    ),
                )
            reply = emitter.final_reply
            if reply and not emitter.error:
                await prepare_reply_audio(user_id, reply)
        finish_task = asyncio.create_task(
            finish_companion_intent(
                user_id,
                trigger,
                reply=reply,
                reply_content=emitter.final_reply_content,
                followup=plan.followup,
                contact_revision=revision,
                user_message_id=message_id,
                original_source=source,
                error=_turn_error(emitter, language),
                tools_started=_tools_may_have_effects(emitter),
            )
        )
        try:
            delivered = await asyncio.shield(finish_task)
        except asyncio.CancelledError:
            delivered = await finish_task
            raise
    except asyncio.CancelledError:
        await finish_companion_intent(
            user_id,
            trigger,
            contact_revision=revision,
            user_message_id=message_id,
            original_source=source,
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
            original_source=source,
            error=_failure_reason(exc, language),
            tools_started=_tools_may_have_effects(emitter),
        )
    finally:
        if reply and not delivered:
            await discard_reply_audio(reply)
