import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict
from functools import partial
from typing import Any

from openai import AsyncOpenAI

from infrastructure.ai.call_context import current_call_task
from infrastructure.ai.error_classifier import (
    ClassifiedError,
    FailoverReason,
    LlmCallBlockedError,
    LLMRuntimeError,
    classify_api_error,
)
from infrastructure.ai.llm_debug import (
    log_event,
    new_call_id,
    summarize_error,
    summarize_llm_request,
    summarize_llm_response,
    truncate_for_log,
)
from infrastructure.ai.providers._audio_responses import AudioChatResponsesClient
from infrastructure.ai.providers._companion_format import ChatSchemaResponsesClient
from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.ai.providers.registry import effective_input_modalities, resolve_context_tokens
from infrastructure.ai.responses import approx_responses_tokens, request_input_modalities
from infrastructure.ai.token_budget import request_output_budget
from infrastructure.ai.usage import LlmUsageAccumulator, current_usage_accumulator, read_llm_usage
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.network import ai_http_timeout

logger = get_logger(__name__)


def _record_usage(
    raw: object,
    *,
    config: ProviderConfig,
    call_id: str,
    call_started: float,
    status: str,
    stream: bool,
    accumulator: LlmUsageAccumulator | None,
    task: str | None,
) -> None:
    usage = read_llm_usage(raw)
    if accumulator is not None:
        accumulator.add(usage)
    logger.info(
        "LLM usage",
        extra={
            "call_id": call_id,
            "provider": config.provider_name,
            "provider_id": str(config.provider_id),
            "model": config.model,
            "model_card_id": str(config.model_card_id),
            "task": task,
            "status": status,
            "stream": stream,
            "latency_ms": int((time.monotonic() - call_started) * 1000),
            "usage": {**asdict(usage), "cache_hit_rate": usage.cache_hit_rate},
        },
    )


async def _guarded_stream(
    stream: Any,
    *,
    call_id: str,
    call_site: str,
    model: str,
    call_started: float,
    config: ProviderConfig,
    accumulator: LlmUsageAccumulator | None,
    task: str | None,
) -> AsyncGenerator[Any]:
    """持续读取模型流；真实异常上抛，结束与取消时关闭底层流。"""
    events_count = 0
    accumulated_content = ""
    usage = None
    finish_reason: Any = None
    function_call_count = 0
    first_chunk_at: float | None = None
    error: BaseException | None = None
    terminal_status: str | None = None
    try:
        while True:
            try:
                chunk = await stream.__anext__()
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
            if event_type in {"response.completed", "response.incomplete", "response.failed"}:
                response = getattr(chunk, "response", None)
                usage = getattr(response, "usage", None) or usage
                terminal_status = event_type.removeprefix("response.")
                details = getattr(response, "incomplete_details", None)
                finish_reason = getattr(details, "reason", None) or getattr(response, "status", None)
            elif (event_usage := getattr(chunk, "usage", None)) is not None:
                usage = event_usage
            if event_type == "error":
                terminal_status = "error"
            yield chunk
    except LlmCallBlockedError as exc:
        error = exc
        raise
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
        # 音视频适配器可能在终态之前获得 Chat 用量，失败时仍保留已报告的数据。
        usage = usage or getattr(stream, "usage", None)
        _record_usage(
            usage,
            config=config,
            call_id=call_id,
            call_started=call_started,
            status=(
                "blocked"
                if isinstance(error, LlmCallBlockedError)
                else "error"
                if error is not None and not isinstance(error, (asyncio.CancelledError, GeneratorExit))
                else terminal_status
                or ("cancelled" if isinstance(error, (asyncio.CancelledError, GeneratorExit)) else "error")
            ),
            stream=True,
            accumulator=accumulator,
            task=task,
        )
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
                response_summary["usage"] = asdict(read_llm_usage(usage))
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
    config: ProviderConfig,
    **create_kwargs: Any,
) -> Any:
    """client.responses.create(**kwargs) 的统一入口：持续等待模型结果，真实异常分类为 LLMRuntimeError。重试由客户端 max_retries 接管（SDK 遵循 Retry-After 并对 408/409/429/5xx 退避）；换供应商由 execute_with_fallback 决定。"""
    context_length = resolve_context_tokens(config)
    if create_kwargs.get("model") != config.model:
        raise ValueError("调用模型必须来自当前能力卡片")
    required_media = request_input_modalities(create_kwargs.get("input"))
    if not required_media <= effective_input_modalities(config):
        raise LLMRuntimeError(
            ClassifiedError(FailoverReason.model_not_found, None, "当前模型卡片不支持请求中的媒体类型"),
        )
    create_kwargs["max_output_tokens"] = request_output_budget(config, create_kwargs)
    if config.provider_name == "mimo" and required_media & {"audio", "video"}:
        if not isinstance(client, AsyncOpenAI):
            raise ValueError("音频请求不能使用文本格式恢复客户端")
        client = AudioChatResponsesClient(client)
    else:
        # 音频时长和转写只参与预算或历史，不发送给供应商。
        def wire(value: Any) -> Any:
            if isinstance(value, list):
                return [wire(item) for item in value]
            if isinstance(value, dict):
                if value.get("type") == "input_audio":
                    return {key: item for key, item in value.items() if key in {"type", "format", "data", "audio_url"}}
                return {key: wire(item) for key, item in value.items()}
            return value

        create_kwargs["input"] = wire(create_kwargs.get("input"))
    create_kwargs["timeout"] = ai_http_timeout()
    model = str(create_kwargs.get("model") or "")
    input_items = create_kwargs.get("input")
    is_stream = bool(create_kwargs.get("stream"))
    call_id = new_call_id()
    # SDK 客户端不携带供应商标签，以 base_url 的 host 标识。
    call_site = client.base_url.host or str(client.base_url)
    call_started = time.monotonic()
    accumulator = current_usage_accumulator()
    task = current_call_task()
    record_usage = partial(
        _record_usage,
        config=config,
        call_id=call_id,
        call_started=call_started,
        stream=is_stream,
        accumulator=accumulator,
        task=task,
    )

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
        task=task,
    )

    try:
        result = await client.responses.create(**create_kwargs, extra_headers={"Idempotency-Key": call_id})
    except (LlmCallBlockedError, asyncio.CancelledError) as exc:
        record_usage(
            None,
            status="cancelled" if isinstance(exc, asyncio.CancelledError) else "blocked",
        )
        raise
    except Exception as exc:
        record_usage(None, status="error")
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
            call_id=call_id,
            call_site=call_site,
            model=model,
            call_started=call_started,
            config=config,
            accumulator=accumulator,
            task=task,
        )
    record_usage(
        getattr(result, "usage", None),
        status=str(getattr(result, "status", "unknown")),
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
