import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from components import DEFAULT_LANGUAGE, TOOL_CALL_ID_HEX_PREFIX_LEN, get_logger, new_request_id
from modules.media import SpeechStyle

from services.infrastructure.llm import (
    FailoverReason,
    LLMRuntimeError,
    ProviderConfig,
    build_responses_kwargs,
    call_with_retry,
    speech_style_guidance,
)

from .bubble import BubbleEvent, BubbleSplitter
from .chat_emitter import Emitter
from .speech_style import SpeechStyleParser
from .system_prompt import refresh_volatile_header_in_prompt

logger = get_logger(__name__)

# 连续助手气泡之间的视觉节奏（plan §2.4）。
BUBBLE_BREAK_MIN_SECONDS = 0.5
BUBBLE_BREAK_MAX_SECONDS = 1.5


class _IncompleteResponseError(RuntimeError):
    def __init__(self, reason: str, *, text_emitted: bool) -> None:
        self.retryable = not text_emitted and reason != "content_filter"
        super().__init__(f"LLM response incomplete: {reason}")


@dataclass
class _LLMTurnResult:
    """单次 LLM 调用的输出：流式文本 + 累积的 tool 调用 + usage；orchestrator 会就地补全 tool_call_id，故不冻结。"""

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int
    reasoning: str | None = None
    speech_style: SpeechStyle | None = None


def _llm_error_user_message(exc: LLMRuntimeError) -> str:
    """为 LLM 错误生成面向用户的提示语；attachment_fetch_failed 给出简短说明，避免暴露内部细节。"""
    if exc.classified.reason == FailoverReason.attachment_fetch_failed:
        return "The LLM provider couldn't fetch the media file attached to this turn. The file may have expired or the URL may not be publicly accessible. Try re-uploading the file."
    return f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """把 LLM 错误转为面向用户的 error 帧；启动期与流中途失败共用，保证本轮始终能收尾。"""
    await emitter.send_json({"type": "error", "message": _llm_error_user_message(exc)})


def _function_call_to_dict(item: Any) -> dict:
    """Responses API 的 ``function_call`` 输出项 → Responses shape dict（与 DB / 工具派发共用）。"""
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return {
        "type": "function_call",
        "call_id": getattr(item, "call_id", ""),
        "name": getattr(item, "name", ""),
        "arguments": getattr(item, "arguments", "{}") or "{}",
    }


def _ensure_tool_call_ids(tool_calls_list: list[dict]) -> None:
    """为每个 tool call 保证唯一非空的 call_id；流式供应商在仅参数增量时常省略 id，重复 id 会合并同一 ipc future 导致 gather 挂起。"""
    seen: set[str] = set()
    for tc in tool_calls_list:
        cid = tc.get("call_id")
        if not isinstance(cid, str) or not cid or cid in seen:
            tc["call_id"] = f"call_{new_request_id()[:TOOL_CALL_ID_HEX_PREFIX_LEN]}"
        seen.add(tc["call_id"])


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


async def _stream_llm_response(
    emitter: Emitter,
    model_name: str,
    context: dict[str, Any],
    active_schemas: list[dict],
    ctx_length: int,
    provider: Any,
    *,
    delivery: Literal["stream", "buffered", "silent", "bubbles"],
    phase_instructions: str = "",
    on_first_chunk: Callable[[], None] | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
    speech_config: ProviderConfig | None = None,
    split_paragraphs: bool = False,
) -> _LLMTurnResult:
    """单次 LLM 调用：流式输出文本、累积 tool 调用、采集 usage；``on_first_chunk`` 仅触发一次，供回退派发器判断能否回退。"""
    client = provider.raw_client()
    reasoning = (
        {"effort": reasoning_effort}
        if reasoning_effort and reasoning_effort in getattr(provider, "REASONING_EFFORTS", frozenset())
        else None
    )
    scaled_temperature = provider.scale_temperature(temperature) if temperature is not None else None
    instructions = refresh_volatile_header_in_prompt(
        context["instructions"],
        user_local_tz=user_local_tz,
        lang=lang,
    )
    if phase_instructions:
        instructions += "\n\n" + phase_instructions
    if speech_config:
        instructions += speech_style_guidance(speech_config.provider_name, speech_config.model)
    speech_parser = SpeechStyleParser(speech_config.provider_name, speech_config.model) if speech_config else None
    kwargs = build_responses_kwargs(
        model=model_name,
        instructions=instructions,
        input_items=context["input"],
        tools=active_schemas,
        stream=True,
        reasoning=reasoning,
        temperature=scaled_temperature,
    )
    if delivery == "bubbles":
        if active_schemas:
            raise ValueError("Bubble delivery requires a tool-free reply")
        kwargs["tool_choice"] = "none"

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
    try:
        stream = await call_with_retry(client, context_length=ctx_length, **kwargs)
    except LLMRuntimeError:
        # 启动期失败：交给 orchestrator 的回退包装器处理，它负责错误事件发出，避免渲染端先看到错误帧又收到下一供应商内容。
        raise

    turn_parts: list[str] = []
    bubble_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls_list: list[dict] = []
    final_prompt_tokens = final_completion_tokens = 0
    final_usage_payload: dict | None = None
    pending_text: list[str] = []

    bubbles = BubbleSplitter(split_paragraphs=split_paragraphs)

    speech_style_sent = False
    text_emitted = False

    async def _send_text(text: str) -> None:
        nonlocal speech_style_sent, text_emitted
        text_emitted = True
        payload = {"type": "chunk", "content": text}
        if not speech_style_sent and speech_parser and speech_parser.style:
            payload["speech_style"] = speech_parser.style.model_dump()
            speech_style_sent = True
        await emitter.send_json(payload)

    async def _emit_bubble_events(events: list[BubbleEvent]) -> None:
        nonlocal speech_style_sent
        for event in events:
            if event.is_break:
                segment = "".join(bubble_parts).strip()
                if not segment:
                    continue
                if delivery == "bubbles":
                    await _send_text(segment)
                speech_style_sent = False
                # --- 分隔符仅作传输用：发 break 帧给渲染端，但不要合并到 turn_content（持久化文本会被 TTS 朗读，不能漏出 ---）。
                turn_parts.append(segment)
                bubble_parts.clear()
                await emitter.send_json({"type": "bubble.break"})
                # 连续气泡间的视觉节奏：让上一个气泡先稳态再开始下一个的流式输出。
                await asyncio.sleep(random.uniform(BUBBLE_BREAK_MIN_SECONDS, BUBBLE_BREAK_MAX_SECONDS))
            elif event.text:
                bubble_parts.append(event.text)
                if delivery != "bubbles":
                    await _send_text(event.text)

    response_finished = False
    try:
        async for chunk in stream:
            # 保持首个供应商事件后禁止回退的边界，独立于正文缓冲。
            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None
            event_type = str(getattr(chunk, "type", ""))
            if event_type == "response.output_text.delta":
                if delivery == "buffered":
                    pending_text.append(chunk.delta)
                elif delivery != "silent":
                    text = speech_parser.feed(chunk.delta) if speech_parser else chunk.delta
                    await _emit_bubble_events(bubbles.feed(text))
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
                item = getattr(chunk, "item", None)
                if item is not None and getattr(item, "type", None) == "function_call":
                    if delivery == "bubbles":
                        raise RuntimeError("LLM returned a tool call during the tool-free reply")
                    tool_calls_list.append(_function_call_to_dict(item))
                elif item is not None and getattr(item, "type", None) == "reasoning":
                    if hasattr(item, "model_dump"):
                        context["input"].append(item.model_dump(exclude_none=True))
                    if not reasoning_parts:
                        extracted = _reasoning_item_text(item)
                        if extracted:
                            reasoning_parts.append(extracted)
                            await emitter.send_json({"type": "reasoning.delta", "content": extracted})
            elif event_type == "response.incomplete":
                details = getattr(getattr(chunk, "response", None), "incomplete_details", None)
                raise _IncompleteResponseError(
                    getattr(details, "reason", None) or "unknown",
                    text_emitted=text_emitted,
                )
            elif event_type == "response.completed":
                response_finished = True
                if usage := getattr(getattr(chunk, "response", None), "usage", None):
                    final_prompt_tokens, final_completion_tokens = usage.input_tokens, usage.output_tokens
                    final_usage_payload = _usage_payload(usage)
            elif event_type == "response.failed":
                response = getattr(chunk, "response", None)
                error = getattr(response, "error", None)
                raise RuntimeError(getattr(error, "message", None) or "LLM response failed")
        if not response_finished:
            raise RuntimeError("LLM stream ended before completion")

        # 陪伴正文等工具结构确定后再交付，防止中间台词进入气泡或 TTS。
        if delivery == "buffered" and not tool_calls_list:
            text = "".join(pending_text)
            await _emit_bubble_events(bubbles.feed(speech_parser.feed(text) if speech_parser else text))
            await _emit_bubble_events(bubbles.flush())
        elif delivery == "bubbles":
            await _emit_bubble_events(bubbles.flush())

    finally:
        await stream.aclose()
        # 工作台已显示的增量在失败时也须收尾；陪伴的未确认正文始终丢弃。
        if delivery == "stream" and (text_emitted or response_finished):
            await _emit_bubble_events(bubbles.flush())

    # 收尾最后气泡：若 break 后立即结束，bubble_parts 为空则不追加，turn_parts 已持有前面气泡。
    if bubble_parts:
        segment = "".join(bubble_parts).strip()
        if segment:
            turn_parts.append(segment)
            if delivery == "bubbles":
                await _send_text(segment)

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    turn_content = "\n\n".join(turn_parts)
    turn_reasoning = "".join(reasoning_parts).strip() or None

    return _LLMTurnResult(
        turn_content=turn_content if not tool_calls_list else "",
        reasoning=turn_reasoning,
        speech_style=speech_parser.style if speech_parser and not tool_calls_list else None,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
    )
