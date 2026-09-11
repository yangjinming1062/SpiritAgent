import os
import threading
import time
from typing import Any

from utils import cfg_bool, cfg_int, cfg_str, load_config

active_environments: dict[str, Any] = {}
last_activity: dict[str, float] = {}
env_lock = threading.Lock()

creation_locks: dict[str, threading.Lock] = {}
creation_locks_lock = threading.Lock()

task_env_overrides: dict[str, dict[str, Any]] = {}
task_env_overrides_lock = threading.Lock()


def _safe_getcwd() -> str:
    try:
        return os.getcwd()
    except FileNotFoundError:
        return os.path.expanduser("~")


def resolve_container_task_id(task_id: str | None) -> str:
    """将 None/空任务 id 归一为 'default'；其他值原样转字符串。"""
    if not task_id:
        return "default"
    return str(task_id)


def get_env_config() -> dict[str, Any]:
    """加载并规范化 `terminal.*` 配置（env_type、cwd、超时、SSH 字段等）。"""
    cfg = load_config() or {}
    t = cfg.get("terminal") if isinstance(cfg, dict) else {}
    t = t if isinstance(t, dict) else {}

    env_type = cfg_str(t, "env_type", "local")
    cwd = cfg_str(t, "cwd", _safe_getcwd() if env_type == "local" else "~")
    if cwd:
        cwd = os.path.expanduser(cwd)
    ssh_cfg = t.get("ssh") if isinstance(t.get("ssh"), dict) else {}
    return {
        "env_type": env_type,
        "cwd": cwd,
        "timeout": cfg_int(t, "timeout", 180),
        "lifetime_seconds": cfg_int(t, "lifetime_seconds", 300),
        "ssh_host": str(ssh_cfg.get("host", "")),
        "ssh_user": str(ssh_cfg.get("user", "")),
        "ssh_port": int(ssh_cfg.get("port", 22)),
        "ssh_key": str(ssh_cfg.get("key", "")),
        "ssh_password": str(ssh_cfg.get("password", "")),
        "ssh_persistent": cfg_bool(t, "ssh_persistent", cfg_bool(ssh_cfg, "persistent", True)),
        "local_persistent": cfg_bool(t, "local_persistent", False),
    }


def get_active_env(task_id: str) -> Any | None:
    """按 task_id 取活跃环境：先归一化再查表，同时兼容原始 key 的旧条目。"""
    lookup = resolve_container_task_id(task_id)
    with env_lock:
        return active_environments.get(lookup) or active_environments.get(task_id)


def is_persistent_env(task_id: str) -> bool:
    """查询指定任务的环境是否声明为持久化（仅看 `_persistent` 字段）。"""
    env = get_active_env(task_id)
    if env is None:
        return False
    return bool(getattr(env, "_persistent", False))


def register_environment(task_id: str, env: Any) -> None:
    """注册一个环境实例并刷新其 last_activity 时间戳。"""
    with env_lock:
        active_environments[task_id] = env
        last_activity[task_id] = time.time()
