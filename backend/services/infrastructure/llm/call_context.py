"""当前模型调用的任务标识，用于日志与用量归属。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from components import AiTaskKey

_TASK: ContextVar[AiTaskKey | None] = ContextVar("ai_call_task", default=None)


@contextmanager
def ai_call_context(task: AiTaskKey | None) -> Iterator[None]:
    token = _TASK.set(task)
    try:
        yield
    finally:
        _TASK.reset(token)


def current_call_task() -> str | None:
    task = _TASK.get()
    return task.value if task is not None else None
