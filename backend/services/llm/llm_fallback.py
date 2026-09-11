import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from components import get_logger, log_paid_call
from sqlalchemy.ext.asyncio import AsyncSession

from .error_classifier import FailoverReason, classify_api_error
from .llm_client import MissingLlmConfigError, resolve_provider_chain
from .llm_debug import log_event, new_call_id
from .providers import BaseProvider, ProviderConfig, resolve

logger = get_logger(__name__)

# 瞬时故障：per-provider 重试层耗尽后允许在链间级联 —— 一次连接抖动不应让用户在引导样图阶段永久丢失一张卡。
# server_error / unknown 不在内：前者通常是供应商特定行为，下家多半同样失败；后者无信号，避免噪声级联。
_CASCADING_TRANSIENT_REASONS: frozenset[FailoverReason] = frozenset({FailoverReason.timeout, FailoverReason.overloaded})


async def execute_with_fallback[T](
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    call_fn: Callable[[BaseProvider], Awaitable[T]],
    *,
    stream_started: Callable[[], bool] | None = None,
    _chain: list[ProviderConfig] | None = None,
) -> T:
    """按 ``resolve_provider_chain`` 解析的供应商链依次调用 ``call_fn``。

    回退触发条件：
    - ``should_fallback=True``：鉴权 / 计费 / 模型缺失 / 内容策略等 *确定性* 失败，直接切下一家；
    - 瞬时故障（``FailoverReason.timeout`` / ``overloaded``）：先由 per-provider 重试层（image_gen 走 [providers/http.py](services/llm/providers/http.py) 的 httpx transport 重试，chat 走 OpenAI SDK 的 max_retries）自行恢复；重试耗尽后再切下一家，避免一次抖动让用户在引导样图阶段永久丢失一张卡。

    流已开始则不再切下一家（半流续不上）。链为空时抛 ``MissingLlmConfigError``。
    """
    chain = _chain if _chain is not None else await resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")

    last_error: Exception | None = None
    content_policy_error: Exception | None = None
    chain_call_id = new_call_id()
    chain_started = time.monotonic()
    chain_size = len(chain)
    for idx, config in enumerate(chain):
        provider_cls = resolve(config.service_type, config.provider_name)
        provider = provider_cls(config)
        # 单次 request/response 日志由 chat 重试包装或 provider 方法自身打；这条链级日志告诉读者当前命中哪个槽以及回退落到哪里。
        log_event(
            call_id=chain_call_id,
            service=service_type,
            provider=config.provider_name,
            model=config.model,
            call_site=__name__,
            phase="chain_attempt",
            chain_index=idx,
            chain_size=chain_size,
            user_id=user_id,
        )
        try:
            started = time.monotonic()
            result = await call_fn(provider)
            # 所有计费能力都从此经过 —— 同步调用无 task_id，paid-calls 面包屑退化为 provider + model + duration。
            duration_ms = round((time.monotonic() - started) * 1000)
            log_paid_call(
                config.provider_name,
                service_type,
                user_id=user_id,
                model=config.model,
                duration_ms=duration_ms,
            )
            log_event(
                call_id=chain_call_id,
                service=service_type,
                provider=config.provider_name,
                model=config.model,
                call_site=__name__,
                phase="chain_result",
                status="success",
                chain_index=idx,
                chain_size=chain_size,
                latency_ms=duration_ms,
                total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
                user_id=user_id,
            )
            return result
        except Exception as exc:
            last_error = exc
            classified = getattr(exc, "classified", None) or classify_api_error(
                exc,
                provider=config.provider_name,
                model=config.model,
            )

            # 记录内容策略拦截：链耗尽时优先抛它而非 last_error，便于调用方执行 prompt 清洗并重试。否则后续供应商级联失败（如视觉 LLM 无法描述参考图）会掩盖真正的可操作根因。
            if classified.reason == FailoverReason.content_policy_blocked:
                content_policy_error = exc

            has_next = idx + 1 < chain_size
            stream_alive = stream_started is not None and stream_started()
            cascade = (
                has_next
                and not stream_alive
                and (classified.should_fallback or classified.reason in _CASCADING_TRANSIENT_REASONS)
            )

            if cascade:
                next_provider = chain[idx + 1].provider_name
                logger.warning(
                    "provider failed; falling back",
                    extra={
                        "service": service_type,
                        "failed_provider": config.provider_name,
                        "model": config.model,
                        "reason": classified.reason.value,
                        "status_code": classified.status_code,
                        # 仅 reason 字段无法操作 —— 某些供应商把根因藏在 200 body（如 MiniMax ``base_resp``）
                        "error": classified.message,
                        "next_provider": next_provider,
                    },
                )
                log_event(
                    call_id=chain_call_id,
                    service=service_type,
                    provider=config.provider_name,
                    model=config.model,
                    call_site=__name__,
                    phase="chain_fallback",
                    status="error",
                    chain_index=idx,
                    chain_size=chain_size,
                    reason=classified.reason.value,
                    status_code=classified.status_code,
                    error_message=classified.message,
                    next_provider=next_provider,
                    user_id=user_id,
                )
                continue

            # 非回退条件 / 流已开始 / 已到末槽：原样抛出原异常（调用方需 ``ClassifiedError`` 时再调 ``classify_api_error``）；若链内有更早的内容策略错误，优先抛它便于调用方的 moderation-retry 触发，而非被级联失败掩盖。
            log_event(
                call_id=chain_call_id,
                service=service_type,
                provider=config.provider_name,
                model=config.model,
                call_site=__name__,
                phase="chain_result",
                status="error",
                chain_index=idx,
                chain_size=chain_size,
                reason=classified.reason.value,
                status_code=classified.status_code,
                error_message=classified.message,
                total_chain_latency_ms=int((time.monotonic() - chain_started) * 1000),
                user_id=user_id,
            )
            raise content_policy_error or last_error


async def execute_stream_with_fallback[T](
    db: AsyncSession | None,
    user_id: int | None,
    service_type: str,
    open_fn: Callable[[BaseProvider], AsyncIterator[T]],
    *,
    _chain: list[ProviderConfig] | None = None,
) -> AsyncIterator[T]:
    """``execute_with_fallback`` 的流式版：回退只发生在首个元素之前——首元素产生即视为流已开始
    （对齐 PROTOCOL §1.6"流已开始不切换"），此后任何失败原样上抛给消费方。

    首元素由 ``execute_with_fallback`` 代为打开（``open_fn`` 调用 + 取首个元素），级联 / 分类 /
    计费日志全部复用其语义——``duration_ms`` 因此记录的是 duration-to-first；本生成器随后接管
    同一迭代器继续产出。
    """
    opened: list[AsyncIterator[T]] = []

    async def open_and_first(provider: BaseProvider) -> T:
        stream = open_fn(provider)
        opened.append(stream)
        try:
            return await anext(stream)
        except StopAsyncIteration:
            # 空流：显式转 RuntimeError，避免 StopAsyncIteration 逃逸进异步生成器框架被隐式转换。
            raise RuntimeError(f"{provider.provider_name} stream ended before first element")

    try:
        first = await execute_with_fallback(db, user_id, service_type, open_and_first, _chain=_chain)
    except Exception:
        # 整链失败：链中各家流尚未被消费，需要在此处关闭避免 HTTP 连接泄漏。
        for stream in opened:
            with contextlib.suppress(Exception):
                await stream.aclose()
        raise
    try:
        yield first
        async for item in opened[-1]:
            yield item
    finally:
        for stream in opened:
            with contextlib.suppress(Exception):
                await stream.aclose()
