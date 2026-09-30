import asyncio
import contextlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from components import TOOL_CALL_ID_HEX_PREFIX_LEN, get_logger, new_request_id, resolve_prompt_text
from modules.conversation import CompanionReply
from prompts.chat import (
    COMPANION_MEDIA_REPLY_GUIDANCES,
    COMPANION_REPAIR_COMPOSE_GUIDANCES,
    COMPANION_REPAIR_SILENCE_GUIDANCES,
    COMPANION_REPLY_CLOSING_GUIDANCES,
    COMPANION_REPLY_GUIDANCES,
    COMPANION_REPLY_REPAIR_GUIDANCES,
    COMPANION_REPLY_TOOL_GUIDANCES,
    COMPANION_TEXT_REPLY_GUIDANCES,
    COMPANION_VOICE_REPLY_GUIDANCES,
)
from pydantic import ValidationError

from services.contracts import MediaTurnState
from services.infrastructure.llm import (
    ChatProvider,
    FailoverReason,
    LLMRuntimeError,
    ProviderConfig,
    build_responses_kwargs,
    call_with_retry,
    resolve_provider_reasoning_effort,
    speech_style_guidance,
)

from .bubble import BubbleEvent, BubbleSplitter
from .chat_emitter import Emitter
from .reply_delivery import companion_reply_schema, parse_companion_reply
from .system_prompt import refresh_volatile_header_in_prompt

logger = get_logger(__name__)

# 连续助手气泡之间的交付节奏。
BUBBLE_BREAK_MIN_SECONDS = 0.5
BUBBLE_BREAK_MAX_SECONDS = 1.5


class _IncompleteResponseError(RuntimeError):
    def __init__(self, reason: str, *, text_emitted: bool) -> None:
        self.retryable = not text_emitted and reason != "content_filter"
        super().__init__(f"LLM response incomplete: {reason}")


class _InvalidCompanionReplyError(RuntimeError):
    def __init__(self, error: ValueError, raw_reply: str) -> None:
        self.raw_reply = raw_reply
        self.validation_errors = (
            error.errors(include_input=False, include_url=False, include_context=False)
            if isinstance(error, ValidationError)
            else [{"type": "value_error", "loc": (), "msg": str(error)}]
        )
        super().__init__("Invalid companion reply format")


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


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """把 LLM 错误转为面向用户的 error 帧；attachment_fetch_failed 给出简短说明，避免暴露内部细节。"""
    message = (
        "The LLM provider couldn't fetch the media file attached to this turn. The file may have expired or the URL may not be publicly accessible. Try re-uploading the file."
        if exc.classified.reason == FailoverReason.attachment_fetch_failed
        else f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"
    )
    await emitter.send_json({"type": "error", "message": message})


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
        payload["reasoning_tokens"] = getattr(details, "reasoning_tokens", 0)
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


def _reply_repair_history(input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留历史顺序与工具结果事实，恢复请求不再携带原生工具续轮帧或中间推理。"""
    return [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": json.dumps({"tool_history": item}, ensure_ascii=False)}],
        }
        if item.get("type") in {"function_call", "function_call_output"}
        else item
        for item in input_items
        if item.get("type") != "reasoning"
    ]


async def _generate_llm_response(
    emitter: Emitter,
    model_name: str,
    context: dict[str, Any],
    active_schemas: list[dict],
    ctx_length: int,
    provider: ChatProvider,
    *,
    delivery: Literal["stream", "buffered", "complete"],
    on_response_started: Callable[[], None] | None,
    reasoning_effort: str,
    temperature: float,
    user_local_tz: str | None,
    lang: str,
    speech_config: ProviderConfig | None,
    reply_preference: Literal["text", "voice"] | None,
    voice_id: str,
    allow_silence: bool,
    reply_format_error: _InvalidCompanionReplyError | None,
    media_turn: MediaTurnState,
) -> _LLMTurnResult:
    """单次 LLM 调用与正文交付；流式首事件或完整响应到达时触发回退哨兵，工具轮正文只在 stream 模式实时显示。``reply_preference`` 非空即陪伴终端回复：非流式取完整数组并按气泡协议校验。"""
    resolved_effort = resolve_provider_reasoning_effort(reasoning_effort, provider.REASONING_EFFORTS)
    reasoning = {"effort": resolved_effort} if resolved_effort else None
    instructions = refresh_volatile_header_in_prompt(
        context["instructions"],
        user_local_tz=user_local_tz,
        lang=lang,
    )
    request_input = context["input"]
    if reply_preference is not None:
        delivery_guidance = resolve_prompt_text(
            COMPANION_VOICE_REPLY_GUIDANCES if speech_config else COMPANION_TEXT_REPLY_GUIDANCES,
            lang,
        ).replace("{preference}", reply_preference)
        reply_guidance = resolve_prompt_text(COMPANION_REPLY_GUIDANCES, lang).replace(
            "{delivery}",
            delivery_guidance,
        )
        if speech_config:
            reply_guidance += speech_style_guidance(speech_config.provider_name, speech_config.model)
        # 没有可引用产物时任何媒体标识都无效，不说明媒体气泡；产物含历史回合中仍可引用的图片与视频。
        if media_turn.artifacts:
            reply_guidance += resolve_prompt_text(COMPANION_MEDIA_REPLY_GUIDANCES, lang)
            reply_guidance += "\n" + json.dumps(
                {
                    "available_media": [
                        {
                            "media_id": a.media_id,
                            "type": a.type,
                            "goal_id": a.goal_id,
                            "status": a.status,
                            "already_delivered": a.bound_message_id is not None,
                        }
                        for a in media_turn.artifacts.values()
                    ],
                    "required_media_goals": sorted(media_turn.required_goals),
                },
                ensure_ascii=False,
            )
        # 部分供应商只允许首条系统消息，回复和修复指令都并入 instructions。
        instructions += reply_guidance
        if reply_format_error is None:
            instructions += resolve_prompt_text(COMPANION_REPLY_TOOL_GUIDANCES, lang) + resolve_prompt_text(
                COMPANION_REPLY_CLOSING_GUIDANCES,
                lang,
            )
        else:
            schema = companion_reply_schema(speech_config, allow_silence=allow_silence)
            # 格式恢复不提供工具；主动回合的恢复仍允许沉默，不能把修复变成一次新的联系。
            instructions += (
                resolve_prompt_text(COMPANION_REPLY_REPAIR_GUIDANCES, lang)
                .replace(
                    "{no_dialogue}",
                    resolve_prompt_text(
                        COMPANION_REPAIR_SILENCE_GUIDANCES if allow_silence else COMPANION_REPAIR_COMPOSE_GUIDANCES,
                        lang,
                    ),
                )
                .replace("{schema}", json.dumps(schema, ensure_ascii=False))
            )
            # 修复资料和阶段指令仅属于本次请求，不进入持久历史或下一轮工具上下文。
            request_input = [
                *_reply_repair_history(request_input),
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "invalid_reply": reply_format_error.raw_reply,
                                    "validation_errors": reply_format_error.validation_errors,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
            ]
    kwargs = build_responses_kwargs(
        model=model_name,
        instructions=instructions,
        input_items=request_input,
        tools=[] if reply_format_error is not None else active_schemas,
        tool_choice="none" if reply_format_error is not None else None,
        stream=delivery != "complete",
        reasoning=reasoning,
        temperature=provider.scale_temperature(temperature),
        text={"format": {"type": "json_object"}}
        if reply_preference is not None and provider.supports_json_array
        else None,
    )

    # 仅记录送往 LLM 的多模态 part 形状：Vertex beta API 400 ``INVALID_ARGUMENT`` 多为代理未能转译 ``inline_data``，通过日志中的实际 part 列表可定位问题而无需抓包。
    image_items = [
        item
        for item in context["input"]
        if isinstance(item.get("content"), list)
        and any(isinstance(part, dict) and part.get("type") == "input_image" for part in item["content"])
    ]
    if image_items:
        logger.info("multimodal request shape", extra={"model_name": model_name, "image_items": len(image_items)})

    turn_start_time = time.monotonic()
    # 请求失败交给编排层处理回退，避免先向客户端报错又交付下一供应商的正文。
    response = await call_with_retry(provider.raw_client(), context_length=ctx_length, **kwargs)

    turn_parts: list[str] = []
    bubble_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_list: list[dict] = []
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None
    pending_text: list[str] = []

    bubbles = BubbleSplitter()

    reply: CompanionReply | None = None
    completed_response: Any = None
    text_emitted = False

    def invalid_reply(error: ValueError, raw_reply: str) -> _InvalidCompanionReplyError:
        exc = _InvalidCompanionReplyError(error, raw_reply)
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
                "repair_attempt": reply_format_error is not None,
                "validation_errors": diagnostics,
                "reply_chars": len(raw_reply),
            },
        )
        return exc

    async def _send_text(text: str) -> None:
        nonlocal text_emitted
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
                # 连续气泡间留出停顿，完整响应也沿用同一交付节奏。
                await asyncio.sleep(random.uniform(BUBBLE_BREAK_MIN_SECONDS, BUBBLE_BREAK_MAX_SECONDS))
            elif event.text:
                bubble_parts.append(event.text)
                await _send_text(event.text)

    async def _collect_output_item(item: Any) -> None:
        if getattr(item, "type", None) == "function_call":
            # Responses shape dict 与 DB / 工具派发共用。
            tool_calls_list.append(item.model_dump(exclude_none=True))
        elif getattr(item, "type", None) == "reasoning":
            if hasattr(item, "model_dump"):
                context["input"].append(item.model_dump(exclude_none=True))
            if delivery == "complete" or not reasoning_parts:
                extracted = _reasoning_item_text(item)
                if extracted:
                    reasoning_parts.append(extracted)
                    await emitter.send_json({"type": "reasoning.delta", "content": extracted})

    if delivery == "complete":
        completed_response = response
        if on_response_started is not None:
            on_response_started()
        if response.status == "incomplete":
            raise _IncompleteResponseError(
                getattr(response.incomplete_details, "reason", None) or "unknown",
                text_emitted=False,
            )
        if response.status != "completed":
            raise RuntimeError(
                getattr(response.error, "message", None) or f"LLM response not completed: {response.status}",
            )
        # 必须先检查全部输出项；完整响应也可能同时包含正文和工具调用。
        for item in response.output:
            await _collect_output_item(item)
        if not tool_calls_list:
            pending_text.append(response.output_text)
        if response.usage:
            final_prompt_tokens, final_completion_tokens = response.usage.input_tokens, response.usage.output_tokens
            final_usage_payload = _usage_payload(response.usage)
    else:
        response_finished = False
        try:
            async for chunk in response:
                # 保持首个供应商事件后禁止回退的边界，独立于正文缓冲。
                if on_response_started is not None:
                    on_response_started()
                    on_response_started = None
                event_type = str(getattr(chunk, "type", ""))
                if event_type == "response.output_text.delta":
                    if delivery == "buffered":
                        pending_text.append(chunk.delta)
                    else:
                        await _emit_bubble_events(bubbles.feed(chunk.delta))
                elif event_type in (
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                    "response.reasoning.delta",
                ):
                    delta = getattr(chunk, "delta", None)
                    if isinstance(delta, str) and delta:
                        reasoning_parts.append(delta)
                        await emitter.send_json({"type": "reasoning.delta", "content": delta})
                elif event_type == "response.output_item.done":
                    await _collect_output_item(getattr(chunk, "item", None))
                elif event_type == "response.incomplete":
                    details = getattr(getattr(chunk, "response", None), "incomplete_details", None)
                    raise _IncompleteResponseError(
                        getattr(details, "reason", None) or "unknown",
                        text_emitted=text_emitted,
                    )
                elif event_type == "response.completed":
                    response_finished = True
                    completed_response = getattr(chunk, "response", None)
                    if usage := getattr(getattr(chunk, "response", None), "usage", None):
                        final_prompt_tokens, final_completion_tokens = usage.input_tokens, usage.output_tokens
                        final_usage_payload = _usage_payload(usage)
                elif event_type == "response.failed":
                    failed_response = getattr(chunk, "response", None)
                    error = getattr(failed_response, "error", None)
                    raise RuntimeError(getattr(error, "message", None) or "LLM response failed")
            if not response_finished:
                raise RuntimeError("LLM stream ended before completion")

        finally:
            await response.aclose()
            # 工作台已显示的增量在失败时也须收尾；缓冲的未确认正文始终丢弃；flush 失败不得覆盖正在传播的原始流异常。
            if delivery == "stream" and (text_emitted or response_finished):
                with contextlib.suppress(Exception):
                    await _emit_bubble_events(bubbles.flush())

    if reply_format_error is not None and tool_calls_list:
        raise invalid_reply(
            ValueError("Tool calls are not allowed during final reply repair"),
            getattr(completed_response, "output_text", "") or "".join(pending_text),
        )

    # 确认完整终态且没有工具调用，才交付正文；工具轮的重叠台词不能先进入气泡或 TTS。
    if delivery != "stream" and not tool_calls_list:
        text = "".join(pending_text)
        if reply_preference is not None:
            try:
                reply = parse_companion_reply(
                    text,
                    speech_config=speech_config,
                    voice_id=voice_id,
                    language=lang,
                    allow_silence=allow_silence,
                    media_turn=media_turn,
                )
            except ValueError as exc:
                raise invalid_reply(exc, text) from exc
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
    turn_reasoning = "".join(reasoning_parts).strip() or None

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
