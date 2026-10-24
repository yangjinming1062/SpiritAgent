"""更新器启动配置；除仓库路径（须按宿主机绝对路径挂载）外全部是容器内固定挂载点。"""

import os
from pathlib import Path

# 与 backend 共享的 Unix socket 卷挂载点。
SOCKET_PATH = Path("/run/updater/updater.sock")
# 状态与任务日志持久卷挂载点。
STATE_DIR = Path("/var/lib/spiritagent-updater")
# 容器回环端口，便于进入容器排查；backend 走共享 Unix socket。
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8097


class UpdaterSettings:
    def __init__(self) -> None:
        repo = os.environ.get("REPO_DIR", "").strip() or "/srv/spiritagent"
        self.repo_dir = Path(repo)
        # 部署密钥与已知主机放仓库 deploy/ 目录，由本进程只读使用。
        self.ssh_key_path = self.repo_dir / "deploy" / "deploy_key"
        self.ssh_known_hosts_path = self.repo_dir / "deploy" / "known_hosts"
        self.socket_path = SOCKET_PATH
        self.state_dir = STATE_DIR
        self.git_timeout_seconds = 120.0
        self.build_timeout_seconds = 1800.0
        self.start_timeout_seconds = 300.0
        # 每任务日志上限，超出后截断并在响应中标记。
        self.log_max_bytes = 5 * 1024 * 1024
        self.task_history_limit = 10


UPDATER_SETTINGS = UpdaterSettings()
