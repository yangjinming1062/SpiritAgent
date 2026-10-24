"""更新器对外契约；Backend 维护同名的管理端镜像模型（跨进程边界，两侧允许版本偏移）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

UpdateStage = Literal[
    "preflight",
    "fetch",
    "fast_forward",
    "build",
    "restart",
    "verify",
    "done",
    "failed",
    "interrupted",
]
TaskKind = Literal["check", "update"]
TaskState = Literal["running", "done", "failed", "interrupted"]


class UpdaterError(BaseModel):
    stage: UpdateStage | None = None
    reason: str
    message: str


class CommitInfo(BaseModel):
    sha: str
    short_sha: str
    subject: str
    author: str
    committed_at: datetime | None = None


class ServiceRuntime(BaseModel):
    """单个业务服务的运行状态与实际运行镜像。"""

    name: str
    state: str = "unknown"
    health: str = "unknown"
    image_id: str = ""
    running_commit: str = ""


class RepoStatus(BaseModel):
    """服务器仓库当前状态；分支、upstream 与提交集合。"""

    branch: str = ""
    upstream: str = ""
    current_commit: str = ""
    current_commit_subject: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    diverged: bool = False
    remote_commit: str = ""
    incoming: list[CommitInfo] = Field(default_factory=list)


class PreflightIssue(BaseModel):
    """检查项失败：git、ssh、docker、compose 或仓库状态不满足更新前提。"""

    check: Literal["git", "ssh", "docker", "compose", "repository", "upstream", "worktree", "divergence"]
    message: str


class CheckResult(BaseModel):
    """检查任务产出：远端可更新提交与前置问题。"""

    repo: RepoStatus
    incoming: list[CommitInfo] = Field(default_factory=list)
    issues: list[PreflightIssue] = Field(default_factory=list)


class UpdaterStatus(BaseModel):
    """GET /status：当前仓库、服务与活动任务。"""

    updater_ready: bool
    version: str = ""
    repo: RepoStatus
    runtime: ServiceRuntime | None = None
    preflight_issues: list[PreflightIssue] = Field(default_factory=list)
    active_task: "UpdaterTaskView | None" = None
    latest_task: "UpdaterTaskView | None" = None


class UpdateRequest(BaseModel):
    """提交更新：目标提交必须仍是当前分支 upstream 的最新提交。"""

    target_commit: str = Field(min_length=7, max_length=40, pattern=r"^[0-9a-fA-F]+$")


class UpdaterTaskView(BaseModel):
    """任务视图：阶段、目标提交、错误与日志指针。"""

    task_id: str
    request_id: str = ""
    kind: TaskKind
    state: TaskState
    stage: UpdateStage = "preflight"
    target_commit: str = ""
    created_at: datetime
    updated_at: datetime
    error: UpdaterError | None = None
    log_offset: int = 0
    log_truncated: bool = False
    check: CheckResult | None = None


class UpdaterTaskList(BaseModel):
    tasks: list[UpdaterTaskView] = Field(default_factory=list)


class TaskAccepted(BaseModel):
    task_id: str
    existing: bool


class UpdaterLogChunk(BaseModel):
    task_id: str
    offset: int
    total_bytes: int
    truncated: bool
    lines: list[str] = Field(default_factory=list)


class UpdaterMessage(BaseModel):
    message: str
