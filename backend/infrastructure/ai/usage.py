"""供应商用量归一化与直接聊天调用的回合统计；不改变模型请求。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    invalid_fields: tuple[str, ...] = ()

    @property
    def cache_hit_rate(self) -> float | None:
        if self.input_tokens and self.cached_input_tokens is not None:
            return self.cached_input_tokens / self.input_tokens
        return None


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def read_llm_usage(raw: object) -> LlmUsage:
    """保留缺失与非法计数为未知；Responses 和 Chat Completions 的输入总数均包含缓存。"""
    invalid: list[str] = []

    def token(name: str, *paths: tuple[str, ...]) -> int | None:
        for path in paths:
            value = raw
            for part in path:
                value = _field(value, part)
            if value is None:
                continue
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9_007_199_254_740_991:
                return value
            invalid.append(name)
            return None
        return None

    input_tokens = token("input_tokens", ("input_tokens",), ("prompt_tokens",))
    cached = token(
        "cached_input_tokens",
        ("input_tokens_details", "cached_tokens"),
        ("prompt_tokens_details", "cached_tokens"),
        ("prompt_cache_hit_tokens",),
    )
    written = token(
        "cache_write_tokens",
        ("input_tokens_details", "cache_write_tokens"),
        ("prompt_tokens_details", "cache_write_tokens"),
        ("cache_creation_input_tokens",),
    )
    output_tokens = token("output_tokens", ("output_tokens",), ("completion_tokens",))
    reasoning = token(
        "reasoning_tokens",
        ("output_tokens_details", "reasoning_tokens"),
        ("completion_tokens_details", "reasoning_tokens"),
    )
    total_tokens = token("total_tokens", ("total_tokens",))
    if input_tokens is not None:
        if cached is not None and cached > input_tokens:
            invalid.append("cached_input_tokens")
            cached = None
        if written is not None and written > input_tokens:
            invalid.append("cache_write_tokens")
            written = None
    if output_tokens is not None and reasoning is not None and reasoning > output_tokens:
        invalid.append("reasoning_tokens")
        reasoning = None
    return LlmUsage(input_tokens, cached, written, output_tokens, reasoning, total_tokens, tuple(invalid))


@dataclass(frozen=True)
class CacheUsageSummary:
    request_count: int
    reported_request_count: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_hit_rate: float | None


@dataclass
class LlmUsageAccumulator:
    request_count: int = 0
    reported_request_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0

    def add(self, usage: LlmUsage) -> None:
        self.request_count += 1
        if usage.input_tokens is not None and usage.cached_input_tokens is not None:
            self.reported_request_count += 1
            self.input_tokens += usage.input_tokens
            self.cached_input_tokens += usage.cached_input_tokens

    def summary(self) -> CacheUsageSummary:
        return CacheUsageSummary(
            request_count=self.request_count,
            reported_request_count=self.reported_request_count,
            input_tokens=self.input_tokens if self.reported_request_count else None,
            cached_input_tokens=self.cached_input_tokens if self.reported_request_count else None,
            cache_hit_rate=self.cached_input_tokens / self.input_tokens if self.input_tokens else None,
        )


_COLLECTOR: ContextVar[tuple[asyncio.Task | None, LlmUsageAccumulator] | None] = ContextVar(
    "chat_llm_usage_collector",
    default=None,
)


@contextmanager
def collect_llm_usage(accumulator: LlmUsageAccumulator) -> Iterator[None]:
    token = _COLLECTOR.set((asyncio.current_task(), accumulator))
    try:
        yield
    finally:
        _COLLECTOR.reset(token)


def current_usage_accumulator() -> LlmUsageAccumulator | None:
    collector = _COLLECTOR.get()
    # 子任务会继承 ContextVar，但其调用不属于父回合的直接模型请求。
    return collector[1] if collector is not None and collector[0] is asyncio.current_task() else None
