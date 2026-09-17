import os
import threading
from typing import Any

from utils import cfg_bool, cfg_int, cfg_str, load_config

active_environments: dict[str, Any] = {}
last_activity: dict[str, float] = {}
env_lock = threading.Lock()

creation_locks: dict[str, threading.Lock] = {}
creation_locks_lock = threading.Lock()


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
    # expanduser 只对 local 生效: SSH 的 `~` 必须原样传给远端 shell, 本地展开会把宿主路径塞进远端 cd。
    cwd = cfg_str(t, "cwd", _safe_getcwd() if env_type == "local" else "~")
    if cwd and env_type == "local":
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
        "ssh_persistent": cfg_bool(t, "ssh_persistent", True),
        "local_persistent": cfg_bool(t, "local_persistent", False),
    }
