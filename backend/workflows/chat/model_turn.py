"""单轮模型调用：供应商回退、末次尝试恢复与已确认上下文交接。"""

from typing import Any, Literal

from features.conversation.audio_input import prepare_audio_input
from features.media.turn_state import MediaTurnState
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.llm_fallback import execute_media_with_fallback
from infrastructure.ai.providers.base import ChatProvider
from infrastructure.ai.providers.registry import resolve_context_tokens
from infrastructure.ai.responses import request_input_modalities
from infrastructure.ai.selection import GenerationOrigin, ProviderSelection, order_provider_chain
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.usage import LlmUsageAccumulator, collect_llm_usage
from infrastructure.database import session_scope
from infrastructure.logger import get_logger

from workflows.chat.chat_emitter import Emitter
from workflows.chat.streaming import _generate_llm_response, _LLMTurnResult, _reply_repair_history
from workflows.chat.turn_errors import CompanionReplyFormatError, FinalReplyStreamError, IncompleteResponseError
from workflows.chat.turn_inputs import InferenceSettings, TurnInputs

logger = get_logger(__name__)


async def generate_turn_response(
    emitter: Emitter,
    *,
    user_id: int,
    session_id: str,
    task: AiTaskKey,
    context: dict[str, Any],
    inputs: TurnInputs,
    inference: InferenceSettings,
    media_turn: MediaTurnState,
    schemas: list[dict],
    request: str,
    delivery: Literal["stream", "buffered", "complete"],
    allow_silence: bool,
    pace_bubbles: bool,
    final_reply_only: bool,
    selection: ProviderSelection | None,
    transcripts: dict[str, str | Exception],
    usage: LlmUsageAccumulator,
) -> _LLMTurnResult:
    """每步刷新模型链；先尝试后续槽位，链尾仅恢复一次，已交付正文禁止回退。"""
    text_delivered = False
    reply_only = final_reply_only

    def set_text_delivered() -> None:
        nonlocal text_delivered
        text_delivered = True

    async def call(provider: ChatProvider, native_audio: bool, has_next_attempt: bool) -> _LLMTurnResult:
        nonlocal reply_only
        input_length = len(context["input"])
        call_context = {
            **context,
            "input": await prepare_audio_input(
                user_id, session_id, context["input"], native=native_audio, transcripts=transcripts
            ),
        }
        recovery_used = False
        reply_format_error: CompanionReplyFormatError | None = None
        while True:
            try:
                with collect_llm_usage(usage):
                    result = await _generate_llm_response(
                        emitter,
                        provider.config.model,
                        call_context,
                        schemas,
                        resolve_context_tokens(provider.config),
                        provider,
                        delivery=delivery,
                        on_text_delivered=set_text_delivered,
                        reasoning_effort=inference.reasoning_effort,
                        temperature=inference.temperature,
                        turn_request=request,
                        user_local_tz=inputs.user_local_tz,
                        lang=inputs.language,
                        speech_config=inputs.speech_config if media_turn.structured_reply else None,
                        reply_preference=inputs.response_preference if media_turn.structured_reply else None,
                        reply_persona=inputs.reply_persona,
                        voice_id=inputs.speech_voice,
                        allow_silence=allow_silence,
                        reply_format_error=reply_format_error,
                        media_turn=media_turn,
                        pace_bubbles=pace_bubbles,
                        final_reply_only=reply_only,
                        allow_voice_fallback=recovery_used,
                    )
                context["input"].extend(call_context["input"][input_length:])
                result.generation_origin = GenerationOrigin.from_config(provider.config)
                return result
            except IncompleteResponseError as exc:
                reply_only = reply_only or exc.final_reply
                if has_next_attempt or recovery_used or not exc.retryable:
                    raise
                recovery_used = True
                logger.warning(
                    "Retrying incomplete response at the final chain attempt",
                    extra={"provider": provider.provider_name, "model": provider.config.model, "error": str(exc)},
                )
            except CompanionReplyFormatError as exc:
                reply_only = True
                if has_next_attempt or recovery_used:
                    raise
                recovery_used = True
                reply_format_error = exc
                logger.warning(
                    "Editing companion reply at the final chain attempt",
                    extra={"provider": provider.provider_name, "model": provider.config.model},
                )
            except FinalReplyStreamError:
                reply_only = True
                raise

    async with session_scope() as db:
        chain = order_provider_chain(await resolve_provider_chain(db, user_id, "llm", task=task), selection)
    return await execute_media_with_fallback(
        chain,
        request_input_modalities(_reply_repair_history(context["input"]) if reply_only else context["input"]),
        call,
        user_id=user_id,
        stream_started=lambda: text_delivered,
    )
