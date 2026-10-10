import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from typing import Any

from components import LLM_RETRY_MIN_TIMEOUT, SETTINGS, get_logger
from openai import AsyncOpenAI

from .error_classifier import LlmCallBlockedError, LLMRuntimeError, classify_api_error
from .llm_debug import (
    log_event,
    new_call_id,
    summarize_error,
    summarize_llm_request,
    summarize_llm_response,
    truncate_for_log,
)
from .providers import ChatSchemaResponsesClient
from .responses import approx_responses_tokens

logger = get_logger(__name__)


async def _guarded_stream(
    stream: Any,
    *,
    budget: float,
    call_id: str,
    call_site: str,
    model: str,
    call_started: float,
) -> AsyncGenerator[Any]:
    """流式迭代：chunk 间静默期受 idle 超时约束、整流受 budget 总预算约束（httpx read 是按次读超时，不等价）；流中异常分类后以 LLMRuntimeError 抛出；结束时关闭底层流避免连接泄漏到 SDK 池。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    idle = float(SETTINGS.llm_stream_idle_timeout_seconds)
    events_count = 0
    accumulated_content = ""
    usage = None
    finish_reason: Any = None
    function_call_count = 0
    first_chunk_at: float | None = None
    error: BaseException | None = None
    try:
        while True:
            remaining_total = deadline - loop.time()
            if remaining_total <= 0:
                raise TimeoutError(f"LLM stream total budget exceeded ({budget}s)")
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=min(idle, remaining_total))
            except StopAsyncIteration:
                return
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            events_count += 1
            event_type = str(getattr(chunk, "type", ""))
            delta = getattr(chunk, "delta", None)
            if isinstance(delta, str) and SETTINGS.llm_debug_logging:
                accumulated_content += delta
            if (
                event_type == "response.output_item.done"
                and getattr(getattr(chunk, "item", None), "type", None) == "function_call"
            ):
                function_call_count += 1
            if event_type in {"response.completed", "response.incomplete"}:
                response = getattr(chunk, "response", None)
                usage = getattr(response, "usage", usage)
                details = getattr(response, "incomplete_details", None)
                finish_reason = getattr(details, "reason", None) or getattr(response, "status", None)
            elif (event_usage := getattr(chunk, "usage", None)) is not None:
                usage = event_usage
            yield chunk
    except Exception as exc:
        classified = classify_api_error(exc)
        logger.warning(
            "LLM stream raised mid-iteration",
            extra={"reason": classified.reason.value, "error_message": classified.message},
        )
        error = LLMRuntimeError(classified)
        raise error from exc
    except BaseException as exc:
        error = exc
        raise
    finally:
        with contextlib.suppress(Exception):
            await stream.close()
        response_summary: dict[str, Any] | None = None
        if SETTINGS.llm_debug_logging:
            preview, original_len = truncate_for_log(accumulated_content)
            response_summary = {
                "num_events": events_count,
                "content_preview": preview,
                "content_original_chars": original_len,
                "finish_reason": finish_reason,
                "function_call_count": function_call_count,
            }
            if usage is not None:
                response_summary["usage"] = {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                }
        extras: dict[str, Any] = {"stream": True}
        if first_chunk_at is not None:
            extras["time_to_first_chunk_ms"] = int((first_chunk_at - call_started) * 1000)
        if error is not None:
            extras["error"] = summarize_error(error)
        log_event(
            call_id=call_id,
            service="llm",
            provider=call_site,
            model=model,
            call_site=call_site,
            phase="error" if error is not None else "response",
            latency_ms=int((time.monotonic() - call_started) * 1000),
            status="error" if error is not None else "success",
            response=response_summary,
            **extras,
        )


async def call_with_retry(
    client: AsyncOpenAI | ChatSchemaResponsesClient,
    *,
    context_length: int = 200000,
    **create_kwargs: Any,
) -> Any:
    """client.responses.create(**kwargs) 的统一入口：终端异常分类为 LLMRuntimeError，流式响应受总预算约束。重试由客户端 max_retries 接管（SDK 遵循 Retry-After 并对 408/409/429/5xx 退避）；换供应商由 execute_with_fallback 决定。"""
    budget = max(float(SETTINGS.llm_request_timeout_seconds), LLM_RETRY_MIN_TIMEOUT)
    model = str(create_kwargs.get("model") or "")
    input_items = create_kwargs.get("input")
    is_stream = bool(create_kwargs.get("stream"))
    call_id = new_call_id()
    # SDK 客户端不携带供应商标签，以 base_url 的 host 标识。
    call_site = client.base_url.host or str(client.base_url)
    call_started = time.monotonic()

    log_event(
        call_id=call_id,
        service="llm",
        provider=call_site,
        model=model,
        call_site=call_site,
        phase="request",
        request=summarize_llm_request(create_kwargs),
        stream=is_stream,
        context_length=context_length,
        timeout_seconds=budget,
    )

    try:
        result = await client.responses.create(**create_kwargs, extra_headers={"Idempotency-Key": call_id})
    except LlmCallBlockedError:
        raise
    except Exception as exc:
        classified = classify_api_error(
            exc,
            approx_tokens=approx_responses_tokens(str(create_kwargs.get("instructions") or ""), input_items),
            context_length=context_length,
            num_messages=len(input_items or []),
        )
        log_event(
            call_id=call_id,
            service="llm",
            provider=call_site,
            model=model,
            call_site=call_site,
            phase="error",
            latency_ms=int((time.monotonic() - call_started) * 1000),
            status="error",
            error={
                **summarize_error(exc),
                "reason": classified.reason.value,
                "should_fallback": classified.should_fallback,
                "classified_message": classified.message,
            },
        )
        raise LLMRuntimeError(classified) from exc

    if is_stream:
        return _guarded_stream(
            result,
            budget=budget,
            call_id=call_id,
            call_site=call_site,
            model=model,
            call_started=call_started,
        )
    log_event(
        call_id=call_id,
        service="llm",
        provider=call_site,
        model=model,
        call_site=call_site,
        phase="response",
        latency_ms=int((time.monotonic() - call_started) * 1000),
        status="success",
        response=summarize_llm_response(result),
        stream=False,
    )
    return result
