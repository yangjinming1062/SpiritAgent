from typing import Any

from ._env_local import LocalEnvironment
from ._env_ssh import SSHEnvironment


def create_environment(
    env_type: str,
    cwd: str,
    timeout: int,
    ssh_config: dict | None = None,
    local_config: dict | None = None,
    task_id: str = "default",
) -> Any:
    """按 env_type 实例化对应的终端环境（local / ssh），并打上 `env_type` 标签。"""
    lc = local_config or {}
    if env_type == "local":
        env = LocalEnvironment(cwd=cwd, timeout=timeout, persistent=lc.get("persistent", False))
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
