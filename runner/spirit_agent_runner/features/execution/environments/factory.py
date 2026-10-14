from spirit_agent_runner.features.execution.environments._env_base import BaseEnvironment, EnvironmentSpec
from spirit_agent_runner.features.execution.environments._env_local import LocalEnvironment
from spirit_agent_runner.features.execution.environments._env_ssh import SSHEnvironment


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
