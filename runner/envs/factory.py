import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from ._env_base import BaseEnvironment, EnvironmentSpec
from ._env_local import LocalEnvironment
from ._env_ssh import SSHEnvironment
from .cleanup import start_cleanup_thread, stop_environment, task_has_active_processes
from .state import (
    active_environments,
    creation_locks,
    creation_locks_lock,
    current_environment_spec,
    env_lock,
    last_activity,
)

logger = logging.getLogger(__name__)


class EnvironmentBusyError(RuntimeError):
    """配置换了执行目标，而旧目标上仍有调用或后台进程在运行。"""


_TARGET_BUSY_MESSAGE = (
    "The terminal target was changed in settings while commands or background processes from the previous "
    "target are still running. Wait for them to finish, or stop background processes with the process tool "
    "(action='list', then action='kill'), then retry."
)


def create_environment(spec: EnvironmentSpec) -> BaseEnvironment:
    """按创建参数实例化终端环境（local / ssh），并记录 `env_type` 与创建参数。"""
    if spec.env_type == "local":
        env = LocalEnvironment(cwd=spec.cwd, timeout=spec.timeout)
    elif spec.env_type == "ssh":
        if spec.ssh is None or not spec.ssh.host or not spec.ssh.user:
            raise ValueError("SSH environment requires ssh_host and ssh_user to be configured")
        env = SSHEnvironment(
            host=spec.ssh.host,
            user=spec.ssh.user,
            port=spec.ssh.port,
            key_path=spec.ssh.key,
            password=spec.ssh.password,
            cwd=spec.cwd,
            timeout=spec.timeout,
        )
    else:
        raise ValueError(f"Unknown environment type: {spec.env_type}. Use 'local' or 'ssh'")
    # local 标签供 file_ops 路由，缺则漏 local 分支。
    env.env_type = spec.env_type
    env.spec = spec
    return env


def _lease(task_id: str, env: BaseEnvironment) -> BaseEnvironment:
    """登记一次持有并刷新活跃时间；调用方须持有 env_lock。"""
    env.leases += 1
    last_activity[task_id] = time.time()
    return env


def _acquire_environment(task_id: str) -> BaseEnvironment:
    """返回并持有与当前配置一致的环境；同一 task 的检查、替换与创建经每任务锁串行。"""
    start_cleanup_thread()
    spec = current_environment_spec()
    with env_lock:
        if (env := active_environments.get(task_id)) is not None and env.spec == spec:
            return _lease(task_id, env)
    with creation_locks_lock:
        task_lock = creation_locks.setdefault(task_id, threading.Lock())
    with task_lock:
        spec = current_environment_spec()
        with env_lock:
            if (env := active_environments.get(task_id)) is not None and env.spec == spec:
                return _lease(task_id, env)
        if env is not None:
            # 进程检查在 env_lock 外；锁内复核租约，空闲判定后不会再租出旧环境。
            processes_active = task_has_active_processes(task_id)
            with env_lock:
                in_use = processes_active or env.in_use
                if in_use and env.spec is not None and env.spec.target == spec.target:
                    # 仅 cwd/超时变化则沿用到空闲。
                    return _lease(task_id, env)
                if not in_use:
                    active_environments.pop(task_id, None)
                    last_activity.pop(task_id, None)
            if in_use:
                raise EnvironmentBusyError(_TARGET_BUSY_MESSAGE)
            logger.info(
                "Terminal settings changed; replacing idle %s environment for task %s",
                env.env_type,
                task_id[:8],
            )
            stop_environment(task_id, env)
        logger.info("Creating new %s environment for task %s...", spec.env_type, task_id[:8])
        env = create_environment(spec)
        with env_lock:
            active_environments[task_id] = env
            _lease(task_id, env)
        logger.info("%s environment ready for task %s", spec.env_type, task_id[:8])
    return env


@contextmanager
def use_environment(task_id: str) -> Iterator[BaseEnvironment]:
    """with 内使用 task 终端环境；持有中不回收。执行目标已变且仍占用则抛 EnvironmentBusyError。"""
    env = _acquire_environment(task_id)
    try:
        yield env
    finally:
        with env_lock:
            env.leases -= 1
