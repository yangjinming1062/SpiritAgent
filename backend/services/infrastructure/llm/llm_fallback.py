import time
from collections.abc import Awaitable, Callable, Sequence
from functools import partial

from components import get_logger, log_paid_call

from .error_classifier import FailoverReason, classify_api_error
from .llm_client import MissingLlmConfigError, build_provider
from .llm_debug import log_event, new_call_id
from .providers import BaseProvider, ProviderConfig

logger = get_logger(__name__)


async def execute_with_fallback[P: BaseProvider, T](
    chain: Sequence[ProviderConfig],
    provider_type: type[P],
    call_fn: Callable[[P], Awaitable[T]],
    *,
    user_id: int | None,
    stream_started: Callable[[], bool] | None = None,
) -> T:
    """按已解析的供应商链依次调用 ``call_fn``；链为空时抛 ``MissingLlmConfigError``。

    分类结果 ``should_fallback`` 时切到下一家（确定性失败，或本家传输层重试已耗尽的超时 / 过载）；
    结果未知、流已开始或已到末槽时抛出。链内出现过内容策略拦截时优先抛它，便于调用方清洗提示词重试，
    而不被后续供应商的级联失败掩盖。
    """
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
        )
        provider = build_provider(config, provider_type)
        log(phase="chain_attempt")
        started = time.monotonic()
        try:
            result = await call_fn(provider)
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
            if (
                next_config is not None
                and classified.should_fallback
                and not (stream_started is not None and stream_started())
            ):
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
            raise content_policy_error or exc
        duration_ms = round((time.monotonic() - started) * 1000)
        # 所有计费能力都从此经过；同步调用无任务 id，按供应商、模型与耗时记账。
        log_paid_call(config.provider_name, service, user_id=user_id, model=config.model, duration_ms=duration_ms)
        log(
            phase="chain_result",
            status="success",
            latency_ms=duration_ms,
            total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
        )
        return result
    raise AssertionError("unreachable: the last chain slot either returns or raises")
