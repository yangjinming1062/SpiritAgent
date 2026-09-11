import atexit
import contextlib
import inspect
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .state import active_environments, creation_locks, creation_locks_lock, env_lock, get_env_config, last_activity

logger = logging.getLogger(__name__)

_cleanup_thread = None
_cleanup_running = False
_cleanup_stop_event = threading.Event()

_cleanup_hooks: list[Callable[[str], None]] = []
_active_process_checkers: list[Callable[[str], bool]] = []


def register_env_cleanup_hook(fn: Callable[[str], None]) -> None:
    """注册环境销毁时的清理回调（如清除文件操作缓存）。"""
    if fn not in _cleanup_hooks:
        _cleanup_hooks.append(fn)


def register_active_process_checker(fn: Callable[[str], bool]) -> None:
    """注册检查指定 task_id 是否存在活跃子进程的回调。"""
    if fn not in _active_process_checkers:
        _active_process_checkers.append(fn)


def _is_already_gone(exc: BaseException) -> bool:
    msg = str(exc)
    return "404" in msg or "not found" in msg.lower()


def _log_cleanup_error(task_id: str, exc: BaseException) -> None:
    if _is_already_gone(exc):
        logger.info("Environment for task %s already cleaned up", task_id)
    else:
        logger.warning("Error cleaning up environment for task %s: %s", task_id, exc)


def _stop_env(env: Any) -> None:
    if hasattr(env, "cleanup"):
        env.cleanup()
    elif hasattr(env, "stop"):
        env.stop()
    elif hasattr(env, "terminate"):
        env.terminate()


def _cleanup_inactive_envs(lifetime_seconds: int = 300) -> None:
    current_time = time.time()
    for task_id in list(last_activity.keys()):
        if any(checker(task_id) for checker in _active_process_checkers):
            last_activity[task_id] = current_time
    envs_to_stop = []
    with env_lock:
        for task_id, last_time in list(last_activity.items()):
            if current_time - last_time > lifetime_seconds:
                env = active_environments.pop(task_id, None)
                last_activity.pop(task_id, None)
                if env is not None:
                    envs_to_stop.append((task_id, env))
        # creation_locks 条目刻意不弹出：删除一个别的线程正在持有的锁对象（环境创建中途），会让第三个线程创建一把新锁进入同一临界区——一个任务两个环境。条目随进程生命周期驻留，由 task_id 空间限定上限。
    for task_id, env in envs_to_stop:
        for hook in _cleanup_hooks:
            with contextlib.suppress(Exception):
                hook(task_id)
        try:
            _stop_env(env)
            logger.info("Cleaned up inactive environment for task: %s", task_id)
        except Exception as e:
            _log_cleanup_error(task_id, e)


def _cleanup_thread_worker() -> None:
    while _cleanup_running:
        try:
            config = get_env_config()
            _cleanup_inactive_envs(config["lifetime_seconds"])
        except Exception as e:
            logger.warning("Error in cleanup thread: %s", e, exc_info=True)
        _cleanup_stop_event.wait(timeout=60)
        _cleanup_stop_event.clear()


def start_cleanup_thread() -> None:
    """惰性启动后台清理线程：仅在首次调用且当前未运行时启动，避免重复。"""
    global _cleanup_thread, _cleanup_running
    with env_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_thread = threading.Thread(target=_cleanup_thread_worker, daemon=True)
            _cleanup_thread.start()


def stop_cleanup_thread() -> None:
    """停止后台清理线程：置标志位 + 唤醒等待 + 限时 join。"""
    global _cleanup_running
    _cleanup_running = False
    _cleanup_stop_event.set()
    if _cleanup_thread is not None:
        with contextlib.suppress(SystemExit, KeyboardInterrupt):
            _cleanup_thread.join(timeout=5)


def cleanup_all_environments() -> int:
    """批量清理所有活跃终端环境；返回已清理任务数。"""
    task_ids = list(active_environments.keys())
    cleaned = 0
    for task_id in task_ids:
        try:
            cleanup_vm(task_id)
            cleaned += 1
        except Exception as e:
            logger.error("Error cleaning %s: %s", task_id, e, exc_info=True)
    if cleaned > 0:
        logger.info("Cleaned %d environments", cleaned)
    return cleaned


def cleanup_vm(task_id: str, *, force_remove: bool = False) -> None:
    """清理指定 task 的终端环境：从活跃表中摘除、清理文件缓存、关闭底层环境。"""
    env = None
    with env_lock:
        env = active_environments.pop(task_id, None)
        last_activity.pop(task_id, None)
    with creation_locks_lock:
        creation_locks.pop(task_id, None)
    for hook in _cleanup_hooks:
        with contextlib.suppress(Exception):
            hook(task_id)
    if env is None:
        return
    try:
        if hasattr(env, "cleanup"):
            sig = inspect.signature(env.cleanup)
            if "force_remove" in sig.parameters:
                env.cleanup(force_remove=force_remove)
            else:
                env.cleanup()
        else:
            _stop_env(env)
        logger.info("Manually cleaned up environment for task: %s", task_id)
    except Exception as e:
        _log_cleanup_error(task_id, e)


def _atexit_cleanup() -> None:
    stop_cleanup_thread()
    if active_environments:
        count = len(active_environments)
        logger.info("Shutting down %d remaining sandbox(es)...", count)
        envs_to_wait = list(active_environments.values())
        cleanup_all_environments()
        for env in envs_to_wait:
            wait_fn = getattr(env, "wait_for_cleanup", None)
            if wait_fn is None:
                continue
            try:
                wait_fn(timeout=15.0)
            except Exception as e:
                logger.debug("wait_for_cleanup raised on exit: %s", e)


atexit.register(_atexit_cleanup)
