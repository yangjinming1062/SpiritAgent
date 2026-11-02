import os
from typing import Any

from spirit_agent_runner.contracts.config import cfg_int, cfg_str
from spirit_agent_runner.features.execution.environments._env_base import EnvironmentSpec, SSHTarget


def _safe_getcwd() -> str:
    try:
        return os.getcwd()
    except FileNotFoundError:
        return os.path.expanduser("~")


def normalize_environment_config(config: dict[str, Any]) -> dict[str, Any]:
    """加载并规范化 `terminal.*` 配置中与执行相关的字段（env_type、cwd、超时、空闲寿命）。"""
    t = config.get("terminal") if isinstance(config, dict) else {}
    t = t if isinstance(t, dict) else {}

    env_type = cfg_str(t, "env_type", "local")
    # SSH 的 ~ 须原样传远端，勿本地 expanduser。
    cwd = cfg_str(t, "cwd", _safe_getcwd() if env_type == "local" else "~")
    if cwd and env_type == "local":
        cwd = os.path.expanduser(cwd)
    return {
        "env_type": env_type,
        "cwd": cwd,
        "timeout": cfg_int(t, "timeout", 180),
        "lifetime_seconds": cfg_int(t, "lifetime_seconds", 300),
    }


def environment_spec(config: dict[str, Any]) -> EnvironmentSpec:
    """当前 terminal 配置对应的环境创建参数；本地环境不带 SSH 字段，修改它们不影响本地环境。"""
    cfg = normalize_environment_config(config)
    ssh = None
    if cfg["env_type"] == "ssh":
        t = config.get("terminal") if isinstance(config, dict) else {}
        ssh_cfg = t.get("ssh") if isinstance(t, dict) and isinstance(t.get("ssh"), dict) else {}
        ssh = SSHTarget(
            host=cfg_str(ssh_cfg, "host"),
            user=cfg_str(ssh_cfg, "user"),
            # 空端口回落默认，勿直接 int()。
            port=cfg_int(ssh_cfg, "port", 22),
            key=cfg_str(ssh_cfg, "key"),
            # 密码不去空白。
            password="" if (password := ssh_cfg.get("password")) is None else str(password),
        )
    return EnvironmentSpec(env_type=cfg["env_type"], cwd=cfg["cwd"], timeout=cfg["timeout"], ssh=ssh)
