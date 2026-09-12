import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from typing import Any

from components import LLM_RETRY_MIN_TIMEOUT, SETTINGS, get_logger

from .error_classifier import classify_api_error
from .llm_debug import (
    log_event,
    new_call_id,
    summarize_error,
    summarize_llm_request,
    summarize_llm_response,
    truncate_for_log,
)
from .responses import approx_responses_tokens

logger = get_logger(__name__)


def _call_site_from_client(client: Any) -> str:
    # SDK client 不携带供应商标签 —— 回退到 base_url 的 host
    base_url = getattr(client, "base_url", None)
    host = getattr(base_url, "host", None) if base_url is not None else None
    return host or (str(base_url) if base_url else "unknown")


class LLMRuntimeError(Exception):
    """包装已分类的 API 错误；原异常挂在 ``__cause__`` 上。"""

    def __init__(self, classified: Any, original: BaseException | None = None) -> None:
        self.classified = classified
        self.original = original
        super().__init__(classified.message or classified.reason.value)


async def _stream_with_timeout(
    stream: Any,
    timeout: float,
    *,
    model: str,
    idle_timeout: float | None = None,
) -> AsyncGenerator[Any]:
    """包装流式响应：每 chunk 间的静默期受 ``idle_timeout`` 约束（重置于每 chunk），
    整个流同时受 ``timeout`` (``llm_request_timeout_seconds``) 总预算硬上界约束。
    finally 中始终 close() 流，避免 chat-loop 取消时 HTTP 连接泄漏到 SDK 池。
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    budget = max(timeout, LLM_RETRY_MIN_TIMEOUT)
    idle = float(idle_timeout if idle_timeout is not None else SETTINGS.llm_stream_idle_timeout_seconds)
    try:
        while True:
            elapsed = loop.time() - start
            remaining_total = budget - elapsed
            if remaining_total <= 0:
                raise TimeoutError(f"LLM stream total budget exceeded ({budget}s)")
            chunk_wait = min(idle, remaining_total)
            try:
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=chunk_wait)
            except StopAsyncIteration:
                return
            yield chunk
    except Exception as exc:
        classified = classify_api_error(exc, model=model)
        logger.warning(
            "LLM stream raised mid-iteration",
            extra={"reason": classified.reason.value, "error_message": classified.message},
        )
        raise LLMRuntimeError(classified, original=exc) from exc
    finally:
        with contextlib.suppress(Exception):
            await stream.close()


async def _wrap_stream_for_debug(
    stream: AsyncGenerator[Any],
    *,
    call_id: str,
    provider: str,
    model: str,
    call_site: str,
    call_started: float,
) -> AsyncGenerator[Any]:
    """透传流并累积 chunk，迭代结束时（成功 / 流中异常 / 取消）统一打一条面包屑。"""
    events_count = 0
    accumulated_content = ""
    usage = None
    finish_reason: Any = None
    function_call_count = 0
    first_chunk_at: float | None = None
    error: BaseException | None = None

    try:
        async for chunk in stream:
            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
            events_count += 1
            event_type = str(getattr(chunk, "type", ""))
            delta = getattr(chunk, "delta", None)
            if isinstance(delta, str):
                accumulated_content += delta
            if event_type == "response.output_item.done":
                item = getattr(chunk, "item", None)
                if getattr(item, "type", None) == "function_call":
                    function_call_count += 1
            if event_type in {"response.completed", "response.incomplete"}:
                response = getattr(chunk, "response", None)
                usage = getattr(response, "usage", usage)
                details = getattr(response, "incomplete_details", None)
                finish_reason = getattr(details, "reason", None) or getattr(response, "status", None)
            else:
                event_usage = getattr(chunk, "usage", None)
                if event_usage is not None:
                    usage = event_usage
            yield chunk
    except asyncio.CancelledError:
        error = asyncio.CancelledError()
        raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        await stream.aclose()
        latency_ms = int((time.monotonic() - call_started) * 1000)
        preview, original_len = truncate_for_log(accumulated_content)
        response_summary: dict[str, Any] = {
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
            log_event(
                call_id=call_id,
                service="llm",
                provider=provider,
                model=model,
                call_site=call_site,
                phase="error",
                latency_ms=latency_ms,
                status="error",
                response=response_summary,
                error=summarize_error(error),
                **extras,
            )
        else:
            log_event(
                call_id=call_id,
                service="llm",
                provider=provider,
                model=model,
                call_site=call_site,
                phase="response",
                latency_ms=latency_ms,
                status="success",
                response=response_summary,
                **extras,
            )


async def call_with_retry(
    client: Any,
    *,
    context_length: int = 200000,
    timeout: float | None = None,
    idle_timeout: float | None = None,
    **create_kwargs: Any,
) -> Any:
    """为 ``client.responses.create(**kwargs)`` 编排 SDK 重试、流式 deadline 与分类。

    重试由构造时的 ``AsyncOpenAI(max_retries=...)`` 接管；SDK 自动遵循 ``Retry-After`` /
    ``retry-after-ms`` 响应头并对 408/409/429/500/502/503/504 退避重试。本函数只在终端异常
    上跑一次分类器，并把结果封装为 :class:`LLMRuntimeError`；后续 fallback 决策交给
    :func:`execute_with_fallback`。流式响应额外受单一 ``timeout`` deadline 约束（httpx
    ``read`` 是按次读超时，不等价），并经 ``_wrap_stream_for_debug`` 在迭代结束时统一发
    面包屑。
    """
    timeout = max(
        timeout if timeout is not None else float(SETTINGS.llm_request_timeout_seconds),
        LLM_RETRY_MIN_TIMEOUT,
    )

    model = str(create_kwargs.get("model") or "")
    input_items = create_kwargs.get("input")
    is_stream = bool(create_kwargs.get("stream"))

    instructions = str(create_kwargs.get("instructions") or "")
    approx_tokens = approx_responses_tokens(instructions, input_items)
    num_messages = len(input_items or [])

    call_id = new_call_id()
    call_site = _call_site_from_client(client)
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
        timeout_seconds=timeout,
    )

    extra_headers = {"Idempotency-Key": call_id}
    classifier_kwargs = {
        "model": model,
        "approx_tokens": approx_tokens,
        "context_length": context_length,
        "num_messages": num_messages,
    }

    try:
        if is_stream:
            raw_stream = await client.responses.create(**create_kwargs, extra_headers=extra_headers)
            deadline_stream = _stream_with_timeout(raw_stream, timeout, model=model, idle_timeout=idle_timeout)
            return _wrap_stream_for_debug(
                deadline_stream,
                call_id=call_id,
                provider=call_site,
                model=model,
                call_site=call_site,
                call_started=call_started,
            )

        result = await client.responses.create(**create_kwargs, extra_headers=extra_headers)
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
    except Exception as exc:
        classified = classify_api_error(exc, **classifier_kwargs)
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
                "retryable": classified.retryable,
                "should_fallback": classified.should_fallback,
                "classified_message": classified.message,
            },
        )
        raise LLMRuntimeError(classified, original=exc) from exc
