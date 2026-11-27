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
    maintain_companion_lease,
)
from features.companion.proactive_runtime import get_user_proactive_record
from features.companion.prompts import PROACTIVE_CONTEXT_LABELS
from features.configuration.settings.values import get_user_setting
from features.conversation.context_window import load_recent_companion_exchange
from features.conversation.evidence import latest_user_message_id
from features.conversation.reply_audio import discard_reply_audio, prepare_reply_audio
from features.conversation.request_contracts import ChatMessageRequest, ChatRequest
from features.conversation.turn_access import ensure_main_conversation
from features.life.context import read_contact_excerpt
from infrastructure.ai.decision import DecisionOption, choose_decision, decision_enabled
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from runtime.desktop.connection import MANAGER
from runtime.user_maintenance import user_request_scope

from workflows.chat.chat_emitter import HeadlessEmitter
from workflows.chat.orchestrator import run_chat_turn

logger = get_logger(__name__)

_FAILURE_REASON_MAX_CHARS = 200
_LIFE_CONTACT_OPTIONS = (
    DecisionOption("consider", "分享内容有新的交流价值，或需要进入完整回合判断。"),
    DecisionOption("quiet", "最近交流已经说过同一段内容，或对方明确表示现在不想收到这类分享。"),
)


async def _decline_redundant_life_contact(experience: dict[str, str], recent: str) -> bool:
    if not recent or not decision_enabled():
        return False
    choice = await choose_decision(
        "companion_life_contact",
        state={
            "title": experience["title"][:100],
            "episode": experience["excerpt"][:360],
            "recent_exchange": recent[-440:],
        },
        instructions=(
            "判断一段伙伴可分享的生活经历此刻是否明显重复或不合适。不打断已有约定。"
            "最近交流已分享同一件事且没有新增内容，或对方明确表示暂时别分享，才选择 quiet。"
            "同主题但有新进展，或资料不足以确定重复时，选择 consider。"
        ),
        options=_LIFE_CONTACT_OPTIONS,
        min_probability=0.90,
    )
    return choice == "quiet"


def _build_proactive_hint(
    intent: CompanionIntentView,
    disturbance_tier: str,
    language: str,
    source: DeferredReplySource | None,
    life_experience: dict[str, str] | None,
    recent_exchange: str,
) -> str:
    data = {"intent": intent.model_dump(mode="json"), "disturbance_tier": disturbance_tier}
    if life_experience is not None:
        data["life_experience"] = life_experience
        data["recent_exchange"] = recent_exchange
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
    if emitter.error_frame is None:
        return None
    message = emitter.error_frame.get("message")
    if isinstance(message, str) and message.strip():
        return redact_sensitive_text(message.strip())[:_FAILURE_REASON_MAX_CHARS]
    return _failure_reason(RuntimeError(), language)


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
    reply_content = None
    delivered = False
    language = "zh"
    source = None
    try:
        remaining = (intent.expires_at - utc_now()).total_seconds()
        async with asyncio.timeout(max(0.0, remaining)), maintain_companion_lease(user_id, trigger):
            async with SESSION_LOCAL() as db:
                if intent.source_kind and intent.source_kind.startswith("reply:"):
                    source = await deferred_reply_source(db, user_id, intent.source_kind)
                    conversation_id = source.conversation_id if source is not None else None
                else:
                    conversation_id = (await ensure_main_conversation(db, user_id)).id
                life_experience = (
                    await read_contact_excerpt(db, user_id, intent.life_event_id) if intent.life_event_id else None
                )
                if intent.source_kind == "life" and life_experience is None:
                    raise LookupError("Selected experience is no longer available")
                recent_exchange = (
                    await load_recent_companion_exchange(db, user_id, current_interaction=False)
                    if life_experience
                    else ""
                )
                message_id = await latest_user_message_id(db, user_id)
                disturbance_tier = await get_disturbance_tier(user_id, db=db)
                language = resolve_language(await get_user_setting(db, user_id, "language"))
            if conversation_id is None:
                await finish_companion_intent(user_id, trigger, contact_revision=revision, user_message_id=message_id)
                return
            if (
                intent.source_kind == "life"
                and life_experience is not None
                and await _decline_redundant_life_contact(life_experience, recent_exchange)
            ):
                await finish_companion_intent(user_id, trigger, contact_revision=revision, user_message_id=message_id)
                return
            request = ChatRequest(
                session_id=str(conversation_id),
                message=ChatMessageRequest(
                    content=_build_proactive_hint(
                        intent, disturbance_tier, language, source, life_experience, recent_exchange
                    )
                ),
            )
            with companion_turn_plan(user_id, intent.id, intent.expires_at) as plan:
                await run_chat_turn(
                    request,
                    user_id,
                    emitter,
                    ephemeral=True,
                    contact_evidence={"selected_experience": life_experience, "recent_dialogue": recent_exchange}
                    if life_experience is not None
                    else None,
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
            reply_content = emitter.final_reply_content
            if reply and not emitter.error:
                await prepare_reply_audio(user_id, reply)
        finish_task = asyncio.create_task(
            finish_companion_intent(
                user_id,
                trigger,
                reply=reply,
                reply_content=reply_content,
                followup=plan.followup,
                contact_revision=revision,
                user_message_id=message_id,
                original_source=source,
                error=_turn_error(emitter, language),
                tools_started=emitter.tools_may_have_effects,
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
            tools_started=emitter.tools_may_have_effects,
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
            tools_started=emitter.tools_may_have_effects,
        )
    finally:
        if reply and not delivered:
            await discard_reply_audio(reply)
