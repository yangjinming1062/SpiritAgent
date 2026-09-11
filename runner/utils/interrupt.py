import logging
import threading
from contextvars import ContextVar

from .config import cfg_get, load_config

logger = logging.getLogger(__name__)


def _debug_interrupt_enabled() -> bool:
    # 每次都重读: Desktop 通过 ``spiritagent.config.update`` 推送开关, 该消息远在导入之后到达。
    return bool(cfg_get(load_config(), "debug", "interrupt", default=False))


# 当前正在执行的 RPC 的 req_id(为空则未进入 execute_tool 路径)。
# 通过 ``process_request`` 的 ``set_current_request`` 设入, ``asyncio.to_thread`` (Python 3.9+)
# 自动 ``copy_context().run`` 复制到 worker 线程 — 同步工具内部 ``is_interrupted()`` 也可拿到。
_current_req_id: ContextVar[str | None] = ContextVar("spiritagent_current_req_id", default=None)


# 仅 set 时存在: req_id -> threading.Event, 表示"该请求被请求方取消"。
# ``set_local_interrupt(req_id, False)`` 会真 pop, 同时清标志位 + 释放内存。
_local_interrupts: dict[str, threading.Event] = {}
_global_interrupt = False
_INTERRUPTS_LOCK = threading.Lock()

_interrupted_threads: set[int] = set()
_LOCK = threading.Lock()


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


def _prune_dead_interrupted_tids() -> None:
    """回收已死线程的 tid: ``set_interrupt(True)`` 加进 set 的 tid 在该线程退出后不会自动清除。

    公开的 ``set_interrupt(active=False)`` 只有在调用方主动传入原 tid 时才会清, 漏调用方就漏清。
    每次 ``is_interrupted()`` 命中热路径时回收一次 — 比定期后台扫描轻, 也避免 set 无限增长。
    """
    if not _interrupted_threads:
        return
    live = {t.ident for t in threading.enumerate()}
    dead = _interrupted_threads - live
    if dead:
        _interrupted_threads.difference_update(dead)


def is_interrupted(req_id: str | None = None) -> bool:
    """无参调用仍可用 — 通过 ContextVar 拿到当前 req_id, 老工具调用点零修改。"""
    rid = req_id if req_id is not None else _current_req_id.get()
    with _LOCK, _INTERRUPTS_LOCK:
        if _global_interrupt:
            return True
        if rid is not None:
            ev = _local_interrupts.get(rid)
            if ev is not None and ev.is_set():
                return True
        current_tid = threading.current_thread().ident
        is_in = current_tid in _interrupted_threads
        # 在持锁状态下做轻量回收, 避免 hot path 上 set 线性增长。
        if not is_in:
            _prune_dead_interrupted_tids()
        return is_in


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    tid = thread_id if thread_id is not None else threading.current_thread().ident
    with _LOCK:
        _interrupted_threads.add(tid) if active else _interrupted_threads.discard(tid)
        _snapshot = (set(_interrupted_threads), _global_interrupt) if _debug_interrupt_enabled() else None
    if _snapshot is not None:
        logger.info(
            "[interrupt-debug] set_interrupt(active=%s, target_tid=%s) called_from_tid=%s current_set=%s",
            active,
            tid,
            threading.current_thread().ident,
            _snapshot,
        )


def set_global_interrupt(active: bool) -> None:
    """进程级兜底默认 False; 保留 API 仅供 ``process_request`` 在新 ``execute_tool`` 入口清残留。"""
    global _global_interrupt
    with _LOCK:
        _global_interrupt = bool(active)
    if _debug_interrupt_enabled():
        logger.info(
            "[interrupt-debug] set_global_interrupt(active=%s) called_from_tid=%s",
            active,
            threading.current_thread().ident,
        )
