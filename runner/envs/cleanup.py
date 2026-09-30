import atexit
import contextlib
import logging
import threading
import time
from collections.abc import Callable

from ._env_base import BaseEnvironment
from .state import active_environments, env_lock, get_env_config, last_activity

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


def task_has_active_processes(task_id: str) -> bool:
    """任务下是否还有运行中的后台进程（按已注册的检查回调）。"""
    return any(checker(task_id) for checker in _active_process_checkers)


def stop_environment(task_id: str, env: BaseEnvironment | None) -> None:
    """运行清理回调并关闭环境；单个回调或环境清理失败只记录，不影响其余清理。"""
    for hook in _cleanup_hooks:
        try:
            hook(task_id)
        except Exception as e:
            logger.warning("Env cleanup hook %r failed for task %s: %s", hook, task_id, e)
    if env is None:
        return
    try:
        env.cleanup()
        logger.info("Cleaned up environment for task: %s", task_id)
    except Exception as e:
        logger.warning("Error cleaning up environment for task %s: %s", task_id, e)


def _cleanup_inactive_envs(lifetime_seconds: int = 300) -> None:
    current_time = time.time()
    for task_id in list(last_activity.keys()):
        # 被调用持有、前台命令执行中或有活跃子进程的环境都要续命, 否则长命令运行中途环境会被回收
        # （SSH 场景下 cleanup 还会掐断 ControlMaster, 杀死在途命令）。
        if _env_busy(task_id) or task_has_active_processes(task_id):
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
        stop_environment(task_id, env)


def _env_busy(task_id: str) -> bool:
    env = active_environments.get(task_id)
    return env is not None and env.in_use


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


def _atexit_cleanup() -> None:
    stop_cleanup_thread()
    with env_lock:
        envs = list(active_environments.items())
        active_environments.clear()
        last_activity.clear()
    if envs:
        logger.info("Shutting down %d remaining environment(s)...", len(envs))
    for task_id, env in envs:
        stop_environment(task_id, env)


atexit.register(_atexit_cleanup)
