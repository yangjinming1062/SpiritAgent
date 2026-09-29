import logging
import threading
import time

from ._env_base import BaseEnvironment
from ._env_local import LocalEnvironment
from ._env_ssh import SSHEnvironment
from .cleanup import start_cleanup_thread
from .state import active_environments, creation_locks, creation_locks_lock, env_lock, get_env_config, last_activity

logger = logging.getLogger(__name__)


def create_environment(
    env_type: str,
    cwd: str,
    timeout: int,
    ssh_config: dict | None = None,
) -> BaseEnvironment:
    """按 env_type 实例化对应的终端环境（local / ssh），并打上 `env_type` 标签。"""
    if env_type == "local":
        env = LocalEnvironment(cwd=cwd, timeout=timeout)
    elif env_type == "ssh":
        if not ssh_config or not ssh_config.get("host") or not ssh_config.get("user"):
            raise ValueError("SSH environment requires ssh_host and ssh_user to be configured")
        env = SSHEnvironment(
            host=ssh_config["host"],
            user=ssh_config["user"],
            port=ssh_config.get("port", 22),
            key_path=ssh_config.get("key", ""),
            password=ssh_config.get("password", ""),
            cwd=cwd,
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unknown environment type: {env_type}. Use 'local' or 'ssh'")
    # file_tools._get_file_ops 通过该标签将 local 路由到 NativeFileOperations；环境类自身不会设置，不补就漏掉 local 分支。
    env.env_type = env_type
    return env


def get_or_create_environment(task_id: str) -> BaseEnvironment:
    """返回 task 的终端环境并刷新活跃时间；缺失时按当前 terminal 配置创建，同一 task 的并发创建经每任务锁串行。"""
    start_cleanup_thread()
    with env_lock:
        if (env := active_environments.get(task_id)) is not None:
            last_activity[task_id] = time.time()
            return env
    with creation_locks_lock:
        task_lock = creation_locks.setdefault(task_id, threading.Lock())
    with task_lock:
        with env_lock:
            if (env := active_environments.get(task_id)) is not None:
                last_activity[task_id] = time.time()
                return env
        config = get_env_config()
        env_type = config["env_type"]
        logger.info("Creating new %s environment for task %s...", env_type, task_id[:8])
        env = create_environment(
            env_type=env_type,
            cwd=config["cwd"],
            timeout=config["timeout"],
            ssh_config={
                "host": config["ssh_host"],
                "user": config["ssh_user"],
                "port": config["ssh_port"],
                "key": config["ssh_key"],
                "password": config["ssh_password"],
            }
            if env_type == "ssh"
            else None,
        )
        with env_lock:
            active_environments[task_id] = env
            last_activity[task_id] = time.time()
        logger.info("%s environment ready for task %s", env_type, task_id[:8])
    return env
