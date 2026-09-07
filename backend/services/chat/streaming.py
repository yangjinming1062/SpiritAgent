import asyncio
import contextlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from components import DEFAULT_LANGUAGE, TOOL_CALL_ID_HEX_PREFIX_LEN, get_logger, new_request_id

from services.llm import FailoverReason, LLMRuntimeError, build_responses_kwargs, call_with_retry

from .affect import AffectScrubber
from .bubble import BubbleEvent, BubbleSplitter
from .chat_emitter import Emitter
from .system_prompt import refresh_volatile_header_in_prompt

logger = get_logger(__name__)

# 连续助手气泡之间的视觉节奏（plan §2.4）。
BUBBLE_BREAK_MIN_SECONDS = 0.5
BUBBLE_BREAK_MAX_SECONDS = 1.5
CHUNK_BATCH_WINDOW_SECONDS = 0.008


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
    emotion: str | None = None
    actions: list[str] | None = None
    spatial_locale: str | None = None
    spatial_target: str | None = None
    mood: str | None = None


def _llm_error_user_message(exc: LLMRuntimeError) -> str:
    """为 LLM 错误生成面向用户的提示语；attachment_fetch_failed 给出简短说明，避免暴露内部细节。"""
    if exc.classified.reason == FailoverReason.attachment_fetch_failed:
        return (
            "The LLM provider couldn't fetch the media file attached to this turn. The file may have expired or the URL may not be publicly accessible. Try re-uploading the file."
        )
    return f"LLM call failed: {exc.classified.reason.value} — {exc.classified.message}"


async def _emit_llm_error(emitter: Emitter, exc: LLMRuntimeError) -> None:
    """把 LLM 错误转为面向用户的 error 帧；启动期与流中途失败共用，保证本轮始终能收尾。"""
    await emitter.send_json({"type": "error", "message": _llm_error_user_message(exc)})


def _function_call_to_dict(item: Any) -> dict:
    """Responses API 的 ``function_call`` 输出项 → Responses shape dict（与 DB / 工具派发共用）。"""
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return {"type": "function_call", "call_id": getattr(item, "call_id", ""), "name": getattr(item, "name", ""), "arguments": getattr(item, "arguments", "{}") or "{}"}


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
    on_first_chunk: Callable[[], None] | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    allowed_emotions: frozenset[str] | None = None,
    allowed_actions: frozenset[str] | None = None,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
) -> _LLMTurnResult:
    """单次 LLM 调用：流式输出文本、累积 tool 调用、采集 usage；``on_first_chunk`` 仅触发一次，供回退派发器判断能否回退。"""
    client = provider.raw_client()
    reasoning = {"effort": reasoning_effort} if reasoning_effort and reasoning_effort in getattr(provider, "REASONING_EFFORTS", frozenset()) else None
    scaled_temperature = provider.scale_temperature(temperature) if temperature is not None else None
    instructions = refresh_volatile_header_in_prompt(
        context["instructions"],
        user_local_tz=user_local_tz,
        lang=lang,
    )
    kwargs = build_responses_kwargs(
        model=model_name,
        instructions=instructions,
        input_items=context["input"],
        tools=active_schemas,
        stream=True,
        reasoning=reasoning,
        temperature=scaled_temperature,
    )

    # 仅记录送往 LLM 的多模态 part 形状：Vertex beta API 400 ``INVALID_ARGUMENT`` 多为代理未能转译 ``inline_data``，通过日志中的实际 part 列表可定位问题而无需抓包。
    image_items = [
        item for item in context["input"] if isinstance(item.get("content"), list) and any(isinstance(part, dict) and part.get("type") == "input_image" for part in item["content"])
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

    message_start_sent = False

    async def _ensure_message_start() -> None:
        nonlocal message_start_sent
        if not message_start_sent:
            message_start_sent = True
            await emitter.send_json({"type": "message.start"})

    affect = AffectScrubber(allowed_emotions, allowed_actions)
    bubbles = BubbleSplitter()

    batch_buf: list[str] = []
    flush_task: asyncio.Task | None = None

    async def _flush_chunk_batch() -> None:
        nonlocal flush_task
        if flush_task is not None and flush_task is not asyncio.current_task():
            flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flush_task
            flush_task = None
        if not batch_buf:
            return
        text = "".join(batch_buf)
        batch_buf.clear()
        await emitter.send_json({"type": "chunk", "content": text})

    async def _timed_flush() -> None:
        try:
            await asyncio.sleep(CHUNK_BATCH_WINDOW_SECONDS)
            await _flush_chunk_batch()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("failed to flush batched chunk", extra={"error": str(e)})
        finally:
            nonlocal flush_task
            if flush_task is asyncio.current_task():
                flush_task = None

    async def _emit_bubble_events(events: list[BubbleEvent]) -> None:
        for event in events:
            if event.is_break:
                await _flush_chunk_batch()
                # --- 分隔符仅作传输用：发 break 帧给渲染端，但不要合并到 turn_content（持久化文本会被 TTS 朗读，不能漏出 ---）。
                if bubble_parts:
                    segment = "".join(bubble_parts).strip()
                    if segment:
                        turn_parts.append(segment)
                    bubble_parts.clear()
                await emitter.send_json({"type": "bubble.break"})
                # 连续气泡间的视觉节奏：让上一个气泡先稳态再开始下一个的流式输出。
                await asyncio.sleep(random.uniform(BUBBLE_BREAK_MIN_SECONDS, BUBBLE_BREAK_MAX_SECONDS))
            elif event.text:
                bubble_parts.append(event.text)
                batch_buf.append(event.text)
                nonlocal flush_task
                if flush_task is None or flush_task.done():
                    flush_task = asyncio.create_task(_timed_flush())

    async def _feed_clean(clean_text: str) -> None:
        if clean_text:
            await _emit_bubble_events(bubbles.feed(clean_text))

    try:
        try:
            async for chunk in stream:
                # 仅含 usage 的 chunk 仍代表流是活的，换供应商会让渲染端内容孤立；在跳过前先触发 on_first_chunk，让回退派发器把流视为已开始。
                if on_first_chunk is not None:
                    on_first_chunk()
                    on_first_chunk = None
                await _ensure_message_start()
                event_type = str(getattr(chunk, "type", ""))
                if event_type == "response.output_text.delta":
                    await _feed_clean(affect.feed(chunk.delta))
                elif event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta", "response.reasoning.delta"):
                    delta = getattr(chunk, "delta", None)
                    if isinstance(delta, str) and delta:
                        reasoning_parts.append(delta)
                        await emitter.send_json({"type": "reasoning.delta", "content": delta})
                elif event_type == "response.output_item.done":
                    item = getattr(chunk, "item", None)
                    if item is not None and getattr(item, "type", None) == "function_call":
                        tool_calls_list.append(_function_call_to_dict(item))
                    elif item is not None and getattr(item, "type", None) == "reasoning":
                        if hasattr(item, "model_dump"):
                            context["input"].append(item.model_dump(exclude_none=True))
                        if not reasoning_parts:
                            extracted = _reasoning_item_text(item)
                            if extracted:
                                reasoning_parts.append(extracted)
                                await emitter.send_json({"type": "reasoning.delta", "content": extracted})
                elif event_type in {"response.completed", "response.incomplete"}:
                    if usage := getattr(getattr(chunk, "response", None), "usage", None):
                        final_prompt_tokens, final_completion_tokens = usage.input_tokens, usage.output_tokens
                        final_usage_payload = _usage_payload(usage)
                elif event_type == "response.failed":
                    response = getattr(chunk, "response", None)
                    error = getattr(response, "error", None)
                    raise RuntimeError(getattr(error, "message", None) or "LLM response failed")
        except LLMRuntimeError:
            # 流中途分类错误：已发出 chunk 后供应商 4xx；orchestrator 看到 stream_emitted=True 拒绝换供应商，抛出此异常并发出收尾 error 帧，让渲染端拿到干净的转写。
            raise

        await _feed_clean(affect.flush())
    finally:
        # 流中途死亡时也要 flush bubble splitter，使残余缓冲文本落地，尾部不完整分隔符会被丢弃。
        await _emit_bubble_events(bubbles.flush())
        await _flush_chunk_batch()

    # 收尾最后气泡：若 break 后立即结束，bubble_parts 为空则不追加，turn_parts 已持有前面气泡。
    if bubble_parts:
        segment = "".join(bubble_parts).strip()
        if segment:
            turn_parts.append(segment)

    turn_duration_ms = int((time.monotonic() - turn_start_time) * 1000)

    turn_content = "\n\n".join(turn_parts)
    turn_reasoning = "".join(reasoning_parts).strip() or None

    return _LLMTurnResult(
        turn_content=turn_content,
        reasoning=turn_reasoning,
        tool_calls_list=tool_calls_list,
        final_prompt_tokens=final_prompt_tokens,
        final_completion_tokens=final_completion_tokens,
        final_usage_payload=final_usage_payload,
        turn_duration_ms=turn_duration_ms,
        emotion=affect.emotion,
        actions=affect.actions,
        spatial_locale=affect.spatial_locale,
        spatial_target=affect.spatial_target,
        mood=affect.mood,
    )
