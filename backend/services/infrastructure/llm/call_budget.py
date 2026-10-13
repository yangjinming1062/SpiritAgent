"""当前模型请求的执行期限；流式首事件后只解除单家等待期限。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from components import AiTaskKey

_ATTEMPT: ContextVar[asyncio.Timeout | None] = ContextVar("ai_attempt_timeout", default=None)
_CHAIN_DEADLINES: ContextVar[tuple[asyncio.Timeout, ...]] = ContextVar("ai_chain_deadlines", default=())
_TASK: ContextVar[AiTaskKey | None] = ContextVar("ai_call_task", default=None)
_REQUEST_TIMEOUT: ContextVar[float | None] = ContextVar("ai_request_timeout", default=None)


@contextmanager
def attempt_budget(timeout: asyncio.Timeout, *, task: AiTaskKey | None, request_timeout: float) -> Iterator[None]:
    token = _ATTEMPT.set(timeout)
    task_token = _TASK.set(task)
    request_token = _REQUEST_TIMEOUT.set(request_timeout)
    try:
        yield
    finally:
        _ATTEMPT.reset(token)
        _TASK.reset(task_token)
        _REQUEST_TIMEOUT.reset(request_token)


def current_call_task() -> str | None:
    task = _TASK.get()
    return task.value if task is not None else None


def provider_request_timeout(default: float) -> float:
    configured = _REQUEST_TIMEOUT.get()
    return configured if configured is not None else default


@contextmanager
def chain_budget(timeout: asyncio.Timeout) -> Iterator[None]:
    token = _CHAIN_DEADLINES.set((*_CHAIN_DEADLINES.get(), timeout))
    try:
        yield
    finally:
        _CHAIN_DEADLINES.reset(token)


def response_started() -> None:
    timeout = _ATTEMPT.get()
    if timeout is not None and not timeout.expired():
        timeout.reschedule(None)


def remaining_chain_seconds(default: float) -> float:
    now = asyncio.get_running_loop().time()
    deadlines = [deadline for timeout in _CHAIN_DEADLINES.get() if (deadline := timeout.when()) is not None]
    return min(default, max(0.0, min(deadlines) - now)) if deadlines else default
