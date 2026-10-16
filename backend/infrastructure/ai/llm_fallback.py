import time
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from infrastructure.ai.call_context import ai_call_context
from infrastructure.ai.error_classifier import FailoverReason, LlmCallBlockedError, classify_api_error
from infrastructure.ai.llm_client import MissingLlmConfigError, build_provider
from infrastructure.ai.llm_debug import log_event, new_call_id
from infrastructure.ai.models import ModelModality
from infrastructure.ai.providers.base import BaseProvider, ChatProvider, ProviderConfig
from infrastructure.ai.providers.registry import effective_input_modalities
from infrastructure.logger import get_logger
from infrastructure.paid_calls import log_paid_call

logger = get_logger(__name__)


async def execute_media_with_fallback[T](
    chain: Sequence[ProviderConfig],
    required_modalities: frozenset[ModelModality],
    call_fn: Callable[[ChatProvider, bool, bool], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None = None,
) -> T:
    """原生音频优先；回调获知是否还有后续尝试，正文交付后不再切换。"""
    text_requirements = required_modalities - {ModelModality.audio}
    native = [config for config in chain if required_modalities <= effective_input_modalities(config)]
    text = [config for config in chain if text_requirements <= effective_input_modalities(config)]
    if ModelModality.audio not in required_modalities:
        return await _execute_with_fallback(
            native,
            ChatProvider,
            lambda provider, has_next: call_fn(provider, False, has_next),
            user_id=user_id,
            stream_started=stream_started,
            prefer_content_policy_error=False,
        )
    if not chain:
        raise MissingLlmConfigError("未配置可用模型")
    if native:
        try:
            return await _execute_with_fallback(
                native,
                ChatProvider,
                lambda provider, has_next: call_fn(provider, True, has_next),
                user_id=user_id,
                stream_started=stream_started,
                has_later_attempts=bool(text),
                prefer_content_policy_error=False,
            )
        except LlmCallBlockedError:
            raise
        except Exception as exc:
            if not classify_api_error(exc).should_fallback or (stream_started is not None and stream_started()):
                raise
    return await _execute_with_fallback(
        text,
        ChatProvider,
        lambda provider, has_next: call_fn(provider, False, has_next),
        user_id=user_id,
        stream_started=stream_started,
        prefer_content_policy_error=False,
    )


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
    return await _execute_with_fallback(
        chain,
        provider_type,
        lambda provider, _has_next: call_fn(provider),
        user_id=user_id,
        stream_started=stream_started,
    )


async def _execute_with_fallback[P: BaseProvider, T](
    chain: Sequence[ProviderConfig],
    provider_type: type[P],
    call_fn: Callable[[P, bool], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None,
    has_later_attempts: bool = False,
    prefer_content_policy_error: bool = True,
) -> T:
    """按链调用；结果未知或流已开始后停止。独立生成默认优先保留内容策略拦截供提示词清洗，聊天按末项实际错误收尾。"""
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
            with ai_call_context(config.task):
                result = await call_fn(provider, idx + 1 < chain_size or has_later_attempts)
        except LlmCallBlockedError:
            raise
        except Exception as exc:
            classified = classify_api_error(exc)
            if prefer_content_policy_error and classified.reason == FailoverReason.content_policy_blocked:
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
