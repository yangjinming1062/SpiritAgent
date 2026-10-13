import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from components import SETTINGS, AiTaskKey, ModelModality, get_logger, log_paid_call

from .call_budget import attempt_budget, chain_budget, remaining_chain_seconds
from .error_classifier import ClassifiedError, FailoverReason, LlmCallBlockedError, LLMRuntimeError, classify_api_error
from .llm_client import MissingLlmConfigError, build_provider
from .llm_debug import log_event, new_call_id
from .providers import (
    BaseProvider,
    ChatProvider,
    ImageGenProvider,
    ProviderConfig,
    ProviderResultUnknownError,
    STTProvider,
    TTSProvider,
    VideoGenProvider,
    effective_input_modalities,
)
from .wait_budget import generation_timeout_scope

logger = get_logger(__name__)


async def execute_media_with_fallback[T](
    chain: Sequence[ProviderConfig],
    required_modalities: frozenset[ModelModality],
    call_fn: Callable[[ChatProvider, bool], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None = None,
) -> T:
    """原生音频优先；转写和文本回退共用同一任务期限，不能跨过流已开始的边界。"""
    text_requirements = required_modalities - {ModelModality.audio}
    native = [config for config in chain if required_modalities <= effective_input_modalities(config)]
    text = [config for config in chain if text_requirements <= effective_input_modalities(config)]
    if ModelModality.audio not in required_modalities:
        return await execute_with_fallback(
            native,
            ChatProvider,
            lambda provider: call_fn(provider, False),
            user_id=user_id,
            stream_started=stream_started,
        )
    if not chain:
        raise MissingLlmConfigError("未配置可用模型")
    total = chain[0].total_timeout_seconds
    if chain[0].fast_decision:
        total = min(total, 20.0) if total is not None else 20.0
    timeout = asyncio.timeout(total)
    try:
        async with timeout:
            with chain_budget(timeout), generation_timeout_scope(timeout):
                if native:
                    try:
                        return await _execute_with_fallback(
                            native,
                            ChatProvider,
                            lambda provider: call_fn(provider, True),
                            user_id=user_id,
                            stream_started=stream_started,
                        )
                    except LlmCallBlockedError:
                        raise
                    except Exception as exc:
                        if not classify_api_error(exc).should_fallback or (
                            stream_started is not None and stream_started()
                        ):
                            raise
                return await _execute_with_fallback(
                    text,
                    ChatProvider,
                    lambda provider: call_fn(provider, False),
                    user_id=user_id,
                    stream_started=stream_started,
                )
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        raise LLMRuntimeError(
            ClassifiedError(FailoverReason.timeout, None, "AI task call budget exhausted"),
        ) from exc


async def execute_with_fallback[P: BaseProvider, T](
    chain: Sequence[ProviderConfig],
    provider_type: type[P],
    call_fn: Callable[[P], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None = None,
) -> T:
    if not chain:
        raise MissingLlmConfigError("no provider configured")
    fast = chain[0].fast_decision
    total = chain[0].total_timeout_seconds
    if fast:
        total = min(total, 20.0) if total is not None else 20.0
    timeout = asyncio.timeout(total)
    try:
        async with timeout:
            with chain_budget(timeout), generation_timeout_scope(timeout):
                return await _execute_with_fallback(
                    chain,
                    provider_type,
                    call_fn,
                    user_id=user_id,
                    stream_started=stream_started,
                )
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        if (
            issubclass(provider_type, (ImageGenProvider, VideoGenProvider, TTSProvider, STTProvider))
            or chain[0].task == AiTaskKey.voice_design
        ):
            raise ProviderResultUnknownError("POST", chain[-1].base_url) from exc
        raise LLMRuntimeError(
            ClassifiedError(FailoverReason.timeout, None, "AI task call budget exhausted"),
        ) from exc


async def _execute_with_fallback[P: BaseProvider, T](
    chain: Sequence[ProviderConfig],
    provider_type: type[P],
    call_fn: Callable[[P], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None,
) -> T:
    """按已解析的供应商链依次调用 call_fn；链为空时抛 MissingLlmConfigError。should_fallback 时切下一家；结果未知、流已开始或已到末槽时抛出。链内出现过内容策略拦截时优先抛它，便于调用方清洗提示词重试；末家结果未知或流已开始时如实抛出，不能被改写成可重发的拦截。"""
    if not chain:
        raise MissingLlmConfigError("no provider configured")

    content_policy_error: Exception | None = None
    chain_call_id = new_call_id()
    chain_started = time.monotonic()
    chain_size = len(chain)
    for idx, config in enumerate(chain):
        service = config.service_type.value
        log = partial(
            log_event,
            call_id=chain_call_id,
            service=service,
            provider=config.provider_name,
            model=config.model,
            call_site=__name__,
            chain_index=idx,
            chain_size=chain_size,
            user_id=user_id,
            task=config.task.value if config.task is not None else None,
            config_source=config.config_source,
        )
        provider = build_provider(config, provider_type)
        log(phase="chain_attempt")
        started = time.monotonic()
        try:
            fast = config.fast_decision
            seconds = config.attempt_timeout_seconds
            if seconds is None:
                seconds = (8.0 if chain_size > 1 else 20.0) if fast else float(SETTINGS.llm_request_timeout_seconds)
            timeout = asyncio.timeout(remaining_chain_seconds(seconds))
            try:
                async with timeout:
                    with (
                        attempt_budget(
                            timeout,
                            task=config.task,
                            request_timeout=max(seconds, float(SETTINGS.llm_request_timeout_seconds)),
                        ),
                        generation_timeout_scope(timeout),
                    ):
                        result = await call_fn(provider)
            except TimeoutError as exc:
                if not timeout.expired():
                    raise
                if (
                    isinstance(provider, (ImageGenProvider, VideoGenProvider, TTSProvider, STTProvider))
                    or config.task == AiTaskKey.voice_design
                ):
                    raise ProviderResultUnknownError("POST", config.base_url) from exc
                raise LLMRuntimeError(
                    ClassifiedError(FailoverReason.timeout, None, "AI provider attempt timed out"),
                ) from exc
        except LlmCallBlockedError:
            raise
        except Exception as exc:
            classified = classify_api_error(exc)
            if classified.reason == FailoverReason.content_policy_blocked:
                content_policy_error = exc
            log_failure = partial(
                log,
                status="error",
                reason=classified.reason.value,
                status_code=classified.status_code,
                error_message=classified.message,
            )
            next_config = chain[idx + 1] if idx + 1 < chain_size else None
            started_streaming = stream_started is not None and stream_started()
            if next_config is not None and classified.should_fallback and not started_streaming:
                logger.warning(
                    "provider failed; falling back",
                    extra={
                        "service": service,
                        "failed_provider": config.provider_name,
                        "model": config.model,
                        "reason": classified.reason.value,
                        "status_code": classified.status_code,
                        # 部分供应商把根因藏在 200 响应体（如 MiniMax base_resp），reason 本身不足以定位。
                        "error": classified.message,
                        "next_provider": next_config.provider_name,
                    },
                )
                log_failure(phase="chain_fallback", next_provider=next_config.provider_name)
                continue
            log_failure(phase="chain_result", total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000))
            if (
                content_policy_error is not None
                and classified.reason != FailoverReason.result_unknown
                and not started_streaming
            ):
                raise content_policy_error
            raise
        duration_ms = round((time.monotonic() - started) * 1000)
        # 经本链完成的计费调用在此记账；同步调用无任务 id，按供应商、模型与耗时记账。
        log_paid_call(config.provider_name, service, user_id=user_id, model=config.model, duration_ms=duration_ms)
        log(
            phase="chain_result",
            status="success",
            latency_ms=duration_ms,
            total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
        )
        return result
    raise AssertionError("unreachable: the last chain slot either returns or raises")
