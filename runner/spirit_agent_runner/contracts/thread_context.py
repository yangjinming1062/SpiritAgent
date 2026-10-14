import contextvars
from collections.abc import Callable
from typing import Any


def propagate_context_to_thread(target: Callable[..., Any]) -> Callable[..., Any]:
    """把调用方的 contextvars（取消事件、学习作用域等）带入自建工作线程；``threading.Thread`` 默认不继承。"""
    ctx = contextvars.copy_context()

    def _runner(*args: Any, **kwargs: Any) -> Any:
        return ctx.run(target, *args, **kwargs)

    return _runner
