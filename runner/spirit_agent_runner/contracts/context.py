from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .tooling import ToolContext

_CURRENT: ContextVar[ToolContext | None] = ContextVar("runner_call", default=None)


def current_context() -> ToolContext:
    context = _CURRENT.get()
    return context if context is not None else ToolContext()


@contextmanager
def invocation_scope(context: ToolContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)
