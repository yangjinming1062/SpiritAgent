import os
import threading
from typing import Any

from utils import cfg_int, cfg_str, load_config

from ._env_base import BaseEnvironment, EnvironmentSpec, SSHTarget

active_environments: dict[str, BaseEnvironment] = {}
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
    ssh_cfg = t.get("ssh")
    if not isinstance(ssh_cfg, dict):
        ssh_cfg = {}
    return {
        "env_type": env_type,
        "cwd": cwd,
        "timeout": cfg_int(t, "timeout", 180),
        "lifetime_seconds": cfg_int(t, "lifetime_seconds", 300),
        "ssh_host": cfg_str(ssh_cfg, "host"),
        "ssh_user": cfg_str(ssh_cfg, "user"),
        # 设置页清空端口时为空串，cfg_int 回落默认端口；直接 int() 会让所有终端调用与清理线程抛错。
        "ssh_port": cfg_int(ssh_cfg, "port", 22),
        "ssh_key": cfg_str(ssh_cfg, "key"),
        # 密码不去首尾空白：空白可能是密码的一部分。
        "ssh_password": "" if (password := ssh_cfg.get("password")) is None else str(password),
    }


def current_environment_spec() -> EnvironmentSpec:
    """当前 terminal 配置对应的环境创建参数；本地环境不带 SSH 字段，修改它们不影响本地环境。"""
    config = get_env_config()
    ssh = (
        SSHTarget(
            host=config["ssh_host"],
            user=config["ssh_user"],
            port=config["ssh_port"],
            key=config["ssh_key"],
            password=config["ssh_password"],
        )
        if config["env_type"] == "ssh"
        else None
    )
    return EnvironmentSpec(env_type=config["env_type"], cwd=config["cwd"], timeout=config["timeout"], ssh=ssh)
