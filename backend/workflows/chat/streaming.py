import asyncio
import contextlib
import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from features.conversation.replies import CompanionReply, LinkBubble
from features.media.turn_state import MediaTurnState
from infrastructure.ai.error_classifier import ClassifiedError, FailoverReason, LLMRuntimeError, classify_api_error
from infrastructure.ai.llm_retry import call_with_retry
from infrastructure.ai.providers.base import ChatProvider, ProviderCapabilityError, ProviderConfig
from infrastructure.ai.providers.speech_style import speech_style_guidance
from infrastructure.ai.response_validation import (
    IncompleteLlmResponseError,
    validate_response,
    validate_response_output,
)
from infrastructure.ai.responses import build_responses_kwargs
from infrastructure.ai.selection import GenerationOrigin
from infrastructure.ai.token_budget import request_output_budget
from infrastructure.constants import TOOL_CALL_ID_HEX_PREFIX_LEN
from infrastructure.correlation import new_request_id
from infrastructure.logger import get_logger
from infrastructure.text import resolve_prompt_text
from openai.types.responses import Response

from workflows.chat.bubble import BubbleEvent, BubbleSplitter
from workflows.chat.chat_emitter import Emitter
from workflows.chat.message_sanitization import replace_media_parts, truncate_responses_context
from workflows.chat.prompts import (
    COMPANION_MEDIA_REPLY_GUIDANCES,
    COMPANION_REPLY_CLOSING_GUIDANCES,
    COMPANION_REPLY_EDIT_GUIDANCES,
    COMPANION_REPLY_GUIDANCES,
    COMPANION_REPLY_INTEGRITY_GUIDANCES,
    COMPANION_REPLY_SCHEMA_GUIDANCES,
    COMPANION_REPLY_TOOL_GUIDANCES,
    COMPANION_TEXT_REPLY_GUIDANCES,
    COMPANION_TOPIC_GUIDANCES,
    COMPANION_VOICE_REPLY_GUIDANCES,
    FINAL_REPLY_REQUEST_LABELS,
    FINAL_REPLY_RETRY_GUIDANCES,
)
from workflows.chat.reply_delivery import (
    companion_reply_schema,
    companion_reply_source,
    normalize_incremental_dialogue_bubble,
    resolve_companion_reply,
)
from workflows.chat.reply_links import ReplyReferences, reply_reference_texts
from workflows.chat.system_prompt import refresh_volatile_header_in_prompt
from workflows.chat.turn_errors import CompanionReplyFormatError, FinalReplyStreamError, IncompleteResponseError
from workflows.chat.types import ReplyBubbleSink

logger = get_logger(__name__)

# 连续助手气泡之间的交付节奏。
BUBBLE_BREAK_MIN_SECONDS = 0.5
BUBBLE_BREAK_MAX_SECONDS = 1.5


class _ReplyBubbleExtractor:
    """Extract closed objects from a streamed companion ``bubbles`` array.

    Extraction is deliberately syntax-only.  A caller must still run the object through the
    normal reply validator before presenting it as spoken content; malformed or incomplete
    objects remain buffered until the terminal response is validated.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._search_from = 0
        self._array_start: int | None = None
        self._cursor = 0
        self._index = 0
        self.kind: str | None = None

    def feed(self, text: str) -> list[tuple[int, dict[str, Any]]]:
        self._buffer += text
        if self.kind is None:
            kind = re.search(r'(?:^|[,{])\s*"kind"\s*:\s*"(dialogue|written)"', self._buffer)
            if kind is not None:
                self.kind = kind.group(1)
        if self.kind is None:
            return []
        if self.kind == "written":
            return []
        if self._array_start is None:
            marker = re.search(r'(?:^|[,{])\s*"bubbles"\s*:\s*\[', self._buffer[self._search_from :])
            if marker is None:
                self._search_from = max(0, len(self._buffer) - 12)
                return []
            self._array_start = self._search_from + marker.end()
            self._cursor = self._array_start

        result: list[tuple[int, dict[str, Any]]] = []
        while True:
            while self._cursor < len(self._buffer) and self._buffer[self._cursor] in " \t\r\n,":
                self._cursor += 1
            if self._cursor >= len(self._buffer) or self._buffer[self._cursor] == "]":
                return result
            if self._buffer[self._cursor] != "{":
                # The terminal validator owns the detailed error; stop extracting on unexpected syntax.
                return result
            end = self._object_end(self._cursor)
            if end is None:
                return result
            raw = self._buffer[self._cursor : end]
            try:
                value = json.loads(raw)
            except ValueError:
                return result
            if not isinstance(value, dict):
                return result
            result.append((self._index, value))
            self._index += 1
            self._cursor = end

    def _object_end(self, start: int) -> int | None:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(self._buffer)):
            char = self._buffer[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None


class _StreamErrorEvent(RuntimeError):
    """流内 ``error`` 事件：SDK 只在载荷带顶层 error 键时抛错，Responses 的 error 事件（code / message）作为普通事件产出。属性按 SDK 异常的形状暴露，供 ``classify_api_error`` 分类。"""

    def __init__(self, code: object, message: object) -> None:
        text = message if isinstance(message, str) and message.strip() else "LLM stream error"
        self.code = code if isinstance(code, str) else None
        self.body = {"code": self.code, "message": text}
        super().__init__(text)


@dataclass
class _LLMTurnResult:
    """单次 LLM 调用的输出：正文、tool 调用与 usage；orchestrator 会就地改写 call_id，故不冻结。"""

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int
    reasoning: str | None = None
    reply: CompanionReply | None = None
    generation_origin: GenerationOrigin | None = None


def _assign_tool_call_ids(tool_calls_list: list[dict]) -> None:
    """为每个 tool call 换上后端 call_id。供应商标识可能缺失/重复（按序号生成），而设备等待表、桌面去重、Runner 日志与截断配对都按 call_id 识别一次调用，必须全局唯一。"""
    for tc in tool_calls_list:
        tc["call_id"] = f"call_{new_request_id()[:TOOL_CALL_ID_HEX_PREFIX_LEN]}"


def _usage_payload(usage: Any) -> dict[str, Any]:
    payload = {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
    if details := getattr(usage, "output_tokens_details", None):
        payload["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
    return payload


def _reasoning_item_text(item: Any) -> str:
    texts: list[str] = []
    for part in getattr(item, "content", None) or []:
        if t := getattr(part, "text", None):
            texts.append(t)
    for part in getattr(item, "summary", None) or []:
        if t := getattr(part, "text", None):
            texts.append(t)
    return "\n\n".join(texts)


def _tool_history_entry(item: dict[str, Any]) -> dict[str, Any]:
    # 工具帧改写为文字资料，多模态结果的媒体只留占位，不把 data URL 当文字写入。
    output = item.get("output")
    return {**item, "output": replace_media_parts(output)} if isinstance(output, list) else item


def _reply_repair_history(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """恢复请求保留历史顺序与工具结果事实，排除原生工具续轮帧和中间推理。"""
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": json.dumps({"tool_history": _tool_history_entry(item)}, ensure_ascii=False),
                }
            ],
        }
        if item.get("type") in {"function_call", "function_call_output"}
        else item
        for item in input_items
        if item.get("type") != "reasoning"
    ]


def _misdirected_reply(tool_calls: list[dict[str, Any]], active_schemas: list[dict]) -> str | None:
    """模型把完整的最终回复对象写成对未提供工具的单个调用（弱模型常见）：返回该参数文本，按回复交付并走同一套校验。"""
    if len(tool_calls) != 1 or tool_calls[0].get("name") in {schema.get("name") for schema in active_schemas}:
        return None
    arguments = tool_calls[0].get("arguments")
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else None
    except ValueError:
        return None
    if (
        isinstance(parsed, dict)
        and parsed.get("kind") in {"dialogue", "written"}
        and isinstance(parsed.get("bubbles"), list)
    ):
        return arguments
    return None


async def _generate_llm_response(
    emitter: Emitter,
    model_name: str,
    context: dict[str, Any],
    active_schemas: list[dict],
    ctx_length: int,
    provider: ChatProvider,
    *,
    delivery: Literal["stream", "buffered", "complete", "reply_stream"],
    on_text_delivered: Callable[[], None] | None,
    reply_bubble_sink: ReplyBubbleSink | None,
    reasoning_effort: str,
    temperature: float,
    turn_request: str,
    user_local_tz: str | None,
    lang: str,
    speech_config: ProviderConfig | None,
    reply_preference: Literal["text", "voice"] | None,
    voice_id: str,
    allow_silence: bool,
    reply_format_error: CompanionReplyFormatError | None,
    media_turn: MediaTurnState,
    pace_bubbles: bool,
    reply_persona: str = "",
    final_reply_only: bool = False,
    allow_voice_fallback: bool = False,
) -> _LLMTurnResult:
    """单次 LLM 调用与正文交付；正文发送时锁定供应商，未确认的正文和推理留在本次尝试。"""
    reasoning = await provider.reasoning_param(reasoning_effort)
    reply_repair = reply_preference is not None and reply_format_error is not None
    # 格式修复接口只接受完整响应，校验修正后的整份草稿后再交付。
    if reply_repair:
        delivery = "complete"
    instructions = (
        resolve_prompt_text(COMPANION_REPLY_EDIT_GUIDANCES, lang)
        if reply_repair
        else refresh_volatile_header_in_prompt(context["instructions"], user_local_tz=user_local_tz, lang=lang)
    )
    final_only = final_reply_only or reply_format_error is not None
    reference_texts = (
        reply_reference_texts(context["input"], turn_request, user_input_indices=context.get("user_input_indices", []))
        if reply_preference is not None
        else ()
    )
    request_input = _reply_repair_history(context["input"]) if final_only and not reply_repair else context["input"]
    if final_reply_only and not reply_repair:
        instructions += resolve_prompt_text(FINAL_REPLY_RETRY_GUIDANCES, lang)
        last_item = context["input"][-1] if context["input"] else {}
        if turn_request.strip() and last_item.get("type") in {"function_call", "function_call_output"}:
            request_input = [
                *request_input,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": resolve_prompt_text(FINAL_REPLY_REQUEST_LABELS, lang) + turn_request,
                        }
                    ],
                },
            ]
    reply_options: dict = {}
    if reply_preference is not None:
        capability_guidance = resolve_prompt_text(COMPANION_REPLY_INTEGRITY_GUIDANCES, lang)
        topic_cards: dict[int, LinkBubble] = context.get("topic_cards", {})
        if topic_cards:
            capability_guidance += resolve_prompt_text(COMPANION_TOPIC_GUIDANCES, lang)
        if speech_config:
            capability_guidance += speech_style_guidance(
                speech_config.provider_name, speech_config.model, language=lang
            )
        # 没有可引用产物时任何媒体标识都无效，不说明媒体气泡；产物含历史回合中仍可引用的图片与视频。
        if media_turn.artifacts:
            capability_guidance += resolve_prompt_text(COMPANION_MEDIA_REPLY_GUIDANCES, lang)
            capability_guidance += "\n" + json.dumps(
                {
                    "available_media": [
                        {
                            "media_id": a.media_id,
                            "type": a.type,
                            "goal_id": a.goal_id,
                            "status": a.status,
                            "already_delivered": a.bound_message_id is not None,
                            **(
                                {
                                    "inspection": {
                                        "verdict": media_turn.inspections[a.media_id].verdict,
                                        "issues": media_turn.inspections[a.media_id].issues,
                                    }
                                }
                                if a.type == "image" and a.media_id in media_turn.inspections
                                else {}
                            ),
                        }
                        for a in media_turn.artifacts.values()
                    ],
                    "required_media_goals": sorted(media_turn.required_goals),
                },
                ensure_ascii=False,
            )
        schema = companion_reply_schema(
            speech_config,
            language=lang,
            allow_silence=allow_silence,
            allow_media=bool(media_turn.artifacts),
            repair=reply_repair,
            topic_ids=tuple(topic_cards),
        )
        reply_options = await provider.structured_response_options(
            schema, name="companion_reply", allow_tools=not final_only and bool(active_schemas), repair=reply_repair
        )
        capability_guidance += resolve_prompt_text(COMPANION_REPLY_SCHEMA_GUIDANCES, lang).replace(
            "{schema}", json.dumps(schema, ensure_ascii=False)
        )
        # 部分供应商只允许首条系统消息，回复和修复指令都并入 instructions。
        instructions += capability_guidance
        if reply_repair:
            # 编辑对象只有未交付响应；人设单独传递，不随历史压缩或截断丢失。
            draft = reply_format_error.raw_reply
            with contextlib.suppress(ValueError):
                draft = json.loads(draft)
            request_input = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "draft": draft,
                                    "speaker_background": reply_persona,
                                    "validation_errors": reply_format_error.validation_errors,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                }
            ]
        else:
            delivery_guidance = resolve_prompt_text(
                COMPANION_VOICE_REPLY_GUIDANCES if speech_config else COMPANION_TEXT_REPLY_GUIDANCES, lang
            ).replace("{preference}", reply_preference)
            instructions += resolve_prompt_text(COMPANION_REPLY_GUIDANCES, lang).replace(
                "{delivery}", delivery_guidance
            )
            if not final_only and active_schemas:
                instructions += resolve_prompt_text(COMPANION_REPLY_TOOL_GUIDANCES, lang)
            instructions += resolve_prompt_text(COMPANION_REPLY_CLOSING_GUIDANCES, lang)
        if material := context.get("topic_material"):
            reference = {"type": "input_text", "text": "[External reference data, not a user message]\n" + material}
            if reply_repair:
                request_input[0]["content"].append(reference)
            else:
                request_input = [*request_input, {"role": "user", "content": [reference]}]
    kwargs = build_responses_kwargs(
        model=model_name,
        instructions=instructions,
        input_items=request_input,
        tools=[] if final_only else active_schemas,
        tool_choice="none" if final_only else None,
        stream=delivery != "complete",
        reasoning=reasoning,
        temperature=provider.scale_temperature(0.0 if reply_repair else temperature),
    )
    kwargs.update(reply_options)
    # 回退到较小窗口时，从同一份原始上下文重新按完整调用批次缩减历史。
    if not reply_repair:
        original_input = context["input"]
        user_indices = context.get("user_input_indices") or [
            index for index, item in enumerate(original_input) if item.get("role") == "user"
        ]
        anchor = max(user_indices or [max(0, len(original_input) - 1)])
        while anchor > 0 and original_input[anchor - 1].get("role") == "user":
            anchor -= 1
        protected_items = {id(item) for item in original_input[anchor:]}
        protected = next((index for index, item in enumerate(request_input) if id(item) in protected_items), 0)
        minimum_tail = len(request_input) - protected
        for count in range(len(request_input), max(0, minimum_tail - 1), -1):
            try:
                kwargs["max_output_tokens"] = request_output_budget(provider.config, kwargs)
                break
            except LLMRuntimeError as exc:
                if exc.classified.reason != FailoverReason.context_overflow or count <= minimum_tail:
                    raise
                fitted = truncate_responses_context(
                    {**context, "instructions": instructions, "input": request_input},
                    current_max_chars=ctx_length // 4,
                    max_recent_items=count - 1,
                    protected_start=protected,
                )
                kwargs["input"] = fitted["input"]

    # 只记录含图片的输入项数量，多模态请求异常时可确认是否带图而无需抓包。
    image_items = [
        item
        for item in request_input
        if isinstance(item.get("content"), list)
        and any(isinstance(part, dict) and part.get("type") == "input_image" for part in item["content"])
    ]
    if image_items:
        logger.info("multimodal request shape", extra={"model_name": model_name, "image_items": len(image_items)})

    turn_start_time = time.monotonic()
    # 请求失败交给编排层处理回退，避免先向客户端报错又交付下一供应商的正文。
    client = provider.structured_response_client(repair=reply_repair)
    response = await call_with_retry(client, config=provider.config, **kwargs)

    turn_parts: list[str] = []
    bubble_parts: list[str] = []
    reasoning_parts: list[str] = []
    pending_reasoning: list[str] = []
    reasoning_items: list[dict[str, Any]] = []
    tool_calls_list: list[dict] = []
    tool_call_started = False
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None
    pending_text: list[str] = []
    partial_bubbles: list[dict[str, Any]] = []
    incremental_receipts: list[tuple[dict[str, Any], asyncio.Future[None]]] = []
    reply_extractor = _ReplyBubbleExtractor() if delivery == "reply_stream" else None
    incremental_index = 0
    incremental_blocked = False

    bubbles = BubbleSplitter()

    reply: CompanionReply | None = None
    completed_response: Any = None
    text_emitted = False

    async def _settle_incremental_receipts() -> None:
        for bubble, receipt in incremental_receipts:
            try:
                await receipt
            except asyncio.CancelledError:
                raise
            except ProviderCapabilityError:
                with contextlib.suppress(ValueError):
                    partial_bubbles.remove(bubble)
            except Exception:
                with contextlib.suppress(ValueError):
                    partial_bubbles.remove(bubble)
        incremental_receipts.clear()

    async def _recover_incremental_reply() -> bool:
        """Turn independently normalized, delivered bubbles into a terminal reply."""
        nonlocal pending_text, reply
        await _settle_incremental_receipts()
        if tool_call_started or tool_calls_list or not partial_bubbles:
            return False
        values = [bubble for bubble in partial_bubbles if bubble.get("type") in {"text", "voice"}]
        if len(values) != len(partial_bubbles):
            return False
        try:
            reply = CompanionReply.model_validate({"bubbles": values})
        except ValueError:
            return False
        pending_text = [companion_reply_source(reply)]
        logger.warning(
            "Companion reply stream failed after incremental delivery; preserving delivered bubbles",
            extra={"bubble_count": len(values)},
        )
        return True

    def _record_usage(usage: Any) -> None:
        """非流式终态与流式 completed 事件共用同一次用量记录。"""
        nonlocal final_prompt_tokens, final_completion_tokens, final_usage_payload
        final_prompt_tokens, final_completion_tokens = usage.input_tokens, usage.output_tokens
        final_usage_payload = {**_usage_payload(usage), "context_window": provider.config.context_window}

    def _log_reply_format_failure(exc: CompanionReplyFormatError) -> None:
        # Pydantic 的 msg 和 extra_forbidden 路径末项仍可能带原始输入，不能写入常规日志。
        diagnostics = [
            {
                "type": detail["type"],
                "loc": (*detail["loc"][:-1], "<extra>") if detail["type"] == "extra_forbidden" else detail["loc"],
            }
            for detail in exc.validation_errors
        ]
        logger.warning(
            "Companion reply validation failed",
            extra={
                "provider": provider.provider_name,
                "model": model_name,
                "response_id": getattr(completed_response, "id", None),
                "status": getattr(completed_response, "status", None),
                "usage": final_usage_payload,
                "repair_attempt": exc.repair_attempt,
                "validation_errors": diagnostics,
                "reply_chars": len(exc.raw_reply),
            },
        )

    async def _flush_reasoning() -> None:
        if pending_reasoning:
            await emitter.send_json({"type": "reasoning.delta", "content": "".join(pending_reasoning)})
            pending_reasoning.clear()

    async def _collect_reasoning(text: str) -> None:
        reasoning_parts.append(text)
        pending_reasoning.append(text)
        if text_emitted:
            await _flush_reasoning()

    async def _send_text(text: str) -> None:
        nonlocal text_emitted
        if not text_emitted:
            text = "".join(bubble_parts)
            if not text.strip():
                return
            if on_text_delivered is not None:
                on_text_delivered()
            await _flush_reasoning()
        text_emitted = True
        payload = {"type": "chunk", "content": text}
        await emitter.send_json(payload)

    async def _emit_bubble_events(events: list[BubbleEvent]) -> None:
        for event in events:
            if event.is_break:
                segment = "".join(bubble_parts).strip()
                if not segment:
                    continue
                # 分隔符仅作传输用：发 break 帧给渲染端，但不要合并到 turn_content（持久化文本会被 TTS 朗读，不能漏出）。
                turn_parts.append(segment)
                bubble_parts.clear()
                await emitter.send_json({"type": "bubble.break"})
                # 连续气泡间留出停顿，完整响应也沿用同一交付节奏；无头回合无人观看，不停顿。
                if pace_bubbles:
                    await asyncio.sleep(random.uniform(BUBBLE_BREAK_MIN_SECONDS, BUBBLE_BREAK_MAX_SECONDS))
            elif event.text:
                bubble_parts.append(event.text)
                await _send_text(event.text)

    async def _emit_reply_bubbles(text: str) -> None:
        nonlocal incremental_blocked, incremental_index, text_emitted
        if reply_extractor is None or reply_bubble_sink is None:
            return
        if incremental_blocked:
            return
        references = ReplyReferences.from_texts(reference_texts)
        extracted = reply_extractor.feed(text)
        if reply_extractor.kind == "written":
            incremental_blocked = True
            return
        for _, bubble in extracted:
            # A media/link bubble, or an invalid dialogue bubble, blocks later bubbles so an
            # incomplete prefix can never be skipped by an independently delivered suffix.
            if bubble.get("type") not in {"text", "voice"}:
                incremental_blocked = True
                continue
            try:
                normalized = normalize_incremental_dialogue_bubble(
                    bubble,
                    speech_config=speech_config,
                    voice_id=voice_id,
                    language=lang,
                    media_turn=media_turn,
                    references=references,
                    topic_cards=context.get("topic_cards", {}),
                )
                if not normalized:
                    incremental_blocked = True
                    continue
                for item in normalized:
                    receipt = await reply_bubble_sink(incremental_index, item)
                    partial_bubbles.append(item)
                    if isinstance(receipt, asyncio.Future):
                        incremental_receipts.append((item, receipt))
                    incremental_index += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                # Partial delivery is an optimization.  A single invalid or unavailable bubble
                # must leave the terminal response available for normal validation/fallback.
                incremental_blocked = True
                logger.warning(
                    "Incremental companion bubble delivery failed", extra={"bubble_index": incremental_index}
                )
                continue
            if not text_emitted and on_text_delivered is not None:
                on_text_delivered()
            text_emitted = True

    def _check_refusal(items: list[Any], *, final_reply: bool) -> None:
        try:
            validate_response_output(items)
        except LLMRuntimeError as exc:
            if final_reply:
                raise FinalReplyStreamError(exc.classified) from exc
            raise

    def _validate_terminal(terminal: Response | None) -> None:
        if terminal is None:
            raise IncompleteResponseError(
                "missing terminal response",
                text_emitted=text_emitted,
                final_reply=_has_final_reply(None),
                retryable=False,
            )
        try:
            validate_response(terminal)
        except IncompleteLlmResponseError as exc:
            raise IncompleteResponseError(
                exc.reason,
                text_emitted=text_emitted,
                final_reply=_has_final_reply(terminal),
                retryable=terminal.status == "incomplete",
            ) from exc
        except LLMRuntimeError as exc:
            if _has_final_reply(terminal):
                raise FinalReplyStreamError(exc.classified) from exc
            raise

    async def _collect_output_item(item: Any) -> None:
        if delivery != "complete":
            _check_refusal([item], final_reply=_has_final_reply(None))
        if getattr(item, "type", None) == "function_call":
            # Responses shape dict 与 DB / 工具派发共用。
            tool_calls_list.append(item.model_dump(exclude_none=True))
        elif getattr(item, "type", None) == "reasoning":
            if hasattr(item, "model_dump"):
                reasoning_items.append(item.model_dump(exclude_none=True))
            if delivery == "complete" or not reasoning_parts:
                extracted = _reasoning_item_text(item)
                if extracted:
                    await _collect_reasoning(extracted)

    def _has_final_reply(response: Any) -> bool:
        has_tools = (
            tool_call_started
            or bool(tool_calls_list)
            or any(getattr(item, "type", None) == "function_call" for item in getattr(response, "output", ()))
        )
        text = getattr(response, "output_text", "") or "".join([*pending_text, *turn_parts, *bubble_parts])
        return not has_tools and bool(text.strip())

    if delivery == "complete":
        completed_response = response
        _validate_terminal(response)
        # 必须先检查全部输出项；完整响应也可能同时包含正文和工具调用。
        for item in response.output:
            await _collect_output_item(item)
        misdirected = _misdirected_reply(tool_calls_list, active_schemas) if reply_preference is not None else None
        if misdirected is not None:
            logger.warning(
                "Companion reply was sent as a tool call; delivering it as the reply",
                extra={
                    "provider": provider.provider_name,
                    "model": model_name,
                    "tool_name": tool_calls_list[0].get("name"),
                },
            )
            tool_calls_list.clear()
            pending_text.append(misdirected)
        elif not tool_calls_list:
            pending_text.append(response.output_text)
        if response.usage:
            _record_usage(response.usage)
    else:
        response_finished = False
        try:
            async for chunk in response:
                event_type = str(getattr(chunk, "type", ""))
                if event_type == "response.output_text.delta":
                    if delivery in {"buffered", "reply_stream"}:
                        pending_text.append(chunk.delta)
                        if delivery == "reply_stream":
                            await _emit_reply_bubbles(chunk.delta)
                    else:
                        await _emit_bubble_events(bubbles.feed(chunk.delta))
                elif event_type in (
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                    "response.reasoning.delta",
                ):
                    delta = getattr(chunk, "delta", None)
                    if isinstance(delta, str) and delta:
                        await _collect_reasoning(delta)
                elif event_type == "response.output_item.done":
                    await _collect_output_item(getattr(chunk, "item", None))
                elif event_type == "response.output_item.added":
                    if getattr(getattr(chunk, "item", None), "type", None) == "function_call":
                        tool_call_started = True
                elif event_type in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
                    tool_call_started = True
                elif event_type in ("response.refusal.delta", "response.refusal.done"):
                    raise LLMRuntimeError(
                        ClassifiedError(FailoverReason.content_policy_blocked, None, "LLM response refused")
                    )
                elif event_type in ("response.completed", "response.incomplete", "response.failed"):
                    completed_response = getattr(chunk, "response", None)
                    _validate_terminal(completed_response)
                    response_finished = True
                    if usage := getattr(completed_response, "usage", None):
                        _record_usage(usage)
                elif event_type == "error":
                    stream_error = _StreamErrorEvent(getattr(chunk, "code", None), getattr(chunk, "message", None))
                    raise LLMRuntimeError(classify_api_error(stream_error)) from stream_error
            if not response_finished:
                raise IncompleteResponseError(
                    "LLM stream ended before completion",
                    text_emitted=text_emitted,
                    final_reply=_has_final_reply(completed_response),
                    retryable=False,
                )

        except LLMRuntimeError as exc:
            if not await _recover_incremental_reply():
                if _has_final_reply(completed_response) and not isinstance(exc, FinalReplyStreamError):
                    raise FinalReplyStreamError(exc.classified) from exc
                raise
        except IncompleteResponseError:
            if not await _recover_incremental_reply():
                raise
        finally:
            try:
                await response.aclose()
            except Exception as exc:
                logger.warning("Failed to close LLM response stream", extra={"error_type": type(exc).__name__})
            # 工作台已显示的增量在失败时也须收尾；缓冲的未确认正文始终丢弃；flush 失败不得覆盖正在传播的原始流异常。
            if delivery == "stream" and (text_emitted or response_finished):
                with contextlib.suppress(Exception):
                    await _emit_bubble_events(bubbles.flush())

    if final_only and tool_calls_list:
        failure = CompanionReplyFormatError(
            ValueError("Tool calls are not allowed during final reply repair"),
            getattr(completed_response, "output_text", "") or "".join(pending_text),
            repair_attempt=reply_format_error is not None,
        )
        _log_reply_format_failure(failure)
        raise failure

    requires_text = reply_preference is None and not media_turn.text_reply_media()
    # 确认完整终态且没有工具调用，才交付正文；工具轮的重叠台词不能先进入气泡或 TTS。
    if delivery != "stream" and not tool_calls_list:
        text = "".join(pending_text)
        if (
            not text.strip()
            and not allow_silence
            and reply_format_error is None
            and (reply_preference is not None or requires_text)
        ):
            # 模型完成响应却没有任何正文：不是可编辑的草稿。先按不完整终态重试；恢复预算用尽后仍为空，
            # 视为没有台词的回复，只交付本回合已生成的媒体（没有媒体则照常报告格式错误）。
            if reply_preference is None or not allow_voice_fallback:
                raise IncompleteResponseError("empty_output", text_emitted=False)
            text = '{"kind":"dialogue","bubbles":[]}'
        if reply_preference is not None:
            # 本回合全部校验共享一次语料扫描；工具轮与失败路径不进入此块，保持零扫描。
            refs = ReplyReferences.from_texts(reference_texts)
            try:
                resolved = resolve_companion_reply(
                    text,
                    speech_config=speech_config,
                    voice_id=voice_id,
                    language=lang,
                    allow_silence=allow_silence,
                    media_turn=media_turn,
                    references=refs,
                    topic_cards=topic_cards,
                    topic_context_at=context.get("topic_context_at"),
                    allow_voice_fallback=allow_voice_fallback,
                    repair=reply_format_error,
                )
            except CompanionReplyFormatError as exc:
                if reply_bubble_sink is None or not partial_bubbles:
                    _log_reply_format_failure(exc)
                    raise
                await _settle_incremental_receipts()
                # 已经实际交付的标准化对话气泡必须有可恢复的事实记录；若后续对象让终态校验失败，
                # 保留这些独立验证过的气泡，避免出现“说过但历史没有”的事实。
                partial_reply = [bubble for bubble in partial_bubbles if bubble.get("type") in {"text", "voice"}]
                if len(partial_reply) != len(partial_bubbles):
                    _log_reply_format_failure(exc)
                    raise
                recovered = CompanionReply.model_validate({"bubbles": partial_reply})
                reply = recovered
                text = companion_reply_source(recovered)
                pending_text = [text]
                logger.warning(
                    "Companion reply terminal validation failed after incremental delivery; preserving bubbles",
                    extra={"bubble_count": len(partial_reply)},
                )
                resolved = None
            if resolved is not None and resolved.degraded:
                logger.warning(
                    "Companion reply degraded after the repair budget was used",
                    extra={
                        "provider": provider.provider_name,
                        "model": model_name,
                        "response_id": getattr(completed_response, "id", None),
                    },
                )
            if resolved is not None:
                text, reply = resolved.text, resolved.reply
                pending_text = [text]
        else:
            await _emit_bubble_events(bubbles.feed(text))
            await _emit_bubble_events(bubbles.flush())

    # 收尾最后气泡：若 break 后立即结束，bubble_parts 为空则不追加，turn_parts 已持有前面气泡。
    if bubble_parts:
        segment = "".join(bubble_parts).strip()
        if segment:
            turn_parts.append(segment)

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    turn_content = "".join(pending_text) if reply is not None else "\n\n".join(turn_parts)
    if requires_text and not tool_calls_list and not turn_content.strip():
        raise IncompleteResponseError("empty_output", text_emitted=text_emitted)
    turn_reasoning = "".join(reasoning_parts).strip() or None
    await _flush_reasoning()
    context["input"].extend(reasoning_items)

    return _LLMTurnResult(
        turn_content=turn_content if not tool_calls_list else "",
        reasoning=turn_reasoning,
        reply=reply,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
    )
