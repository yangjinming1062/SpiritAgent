import threading
from contextvars import ContextVar

# 当前正在执行的 RPC 的 req_id(为空则未进入 execute_tool 路径)。
# 通过 ``process_request`` 的 ``set_current_request`` 设入, ``asyncio.to_thread`` (Python 3.9+)
# 自动 ``copy_context().run`` 复制到 worker 线程 — 同步工具内部 ``is_interrupted()`` 也可拿到。
_current_req_id: ContextVar[str | None] = ContextVar("spiritagent_current_req_id", default=None)

# 仅 set 时存在: req_id -> threading.Event, 表示"该请求被请求方取消"。
# ``set_local_interrupt(req_id, False)`` 会真 pop, 同时清标志位 + 释放内存。
_local_interrupts: dict[str, threading.Event] = {}
_INTERRUPTS_LOCK = threading.Lock()


def set_current_request(req_id: str | None) -> object:
    """``process_request`` 进入 ``execute_tool`` 分支前调用; 返回 ``ContextVar.Token`` 用于 finally。"""
    return _current_req_id.set(req_id)


def reset_current_request(token: object) -> None:
    _current_req_id.reset(token)  # type: ignore[arg-type]


def set_local_interrupt(req_id: str | None, active: bool) -> None:
    """置 / 清某条 RPC 的 per-req 取消标志。无 req_id 时回落到当前 ContextVar 中的 req_id。"""
    rid = req_id if req_id is not None else _current_req_id.get()
    if rid is None:
        return
    with _INTERRUPTS_LOCK:
        if active:
            _local_interrupts.setdefault(rid, threading.Event()).set()
        else:
            _local_interrupts.pop(rid, None)


def is_interrupted() -> bool:
    """当前请求是否已被请求方取消; 未进入 execute_tool 路径时恒为 False。"""
    rid = _current_req_id.get()
    with _INTERRUPTS_LOCK:
        return rid is not None and (ev := _local_interrupts.get(rid)) is not None and ev.is_set()
