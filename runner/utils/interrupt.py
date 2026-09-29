import threading
from contextvars import ContextVar, Token

# 当前工具调用的取消事件；asyncio.to_thread 与 tools.thread_context 会把它复制到工作线程，
# 同步工具内部可经 is_interrupted() 感知取消。
_current_cancel_event: ContextVar[threading.Event | None] = ContextVar("spiritagent_cancel_event", default=None)


def set_cancel_event(event: threading.Event) -> Token[threading.Event | None]:
    """绑定当前调用的取消事件；返回值交给 ``reset_cancel_event`` 在调用结束时恢复。"""
    return _current_cancel_event.set(event)


def reset_cancel_event(token: Token[threading.Event | None]) -> None:
    _current_cancel_event.reset(token)


def is_interrupted() -> bool:
    """当前调用是否已被请求方取消；不在工具调用内时恒为 False。"""
    return (event := _current_cancel_event.get()) is not None and event.is_set()
