"""服务更新的管理端契约；更新器侧对应 updater/contracts.py。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ServiceUpdateStage = Literal[
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


class ServiceCommitInfo(BaseModel):
    sha: str
    short_sha: str
    subject: str
    author: str
    committed_at: datetime | None = None


class ServiceRuntimeView(BaseModel):
    name: str
    state: str = "unknown"
    health: str = "unknown"
    image_id: str = ""
    running_commit: str = ""


class ServiceRepoView(BaseModel):
    branch: str = ""
    upstream: str = ""
    current_commit: str = ""
    current_commit_subject: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    diverged: bool = False
    remote_commit: str = ""


class ServicePreflightIssue(BaseModel):
    check: Literal["git", "ssh", "docker", "compose", "repository", "upstream", "worktree", "divergence"]
    message: str


class ServiceUpdateTaskError(BaseModel):
    stage: ServiceUpdateStage | None = None
    reason: str
    message: str


class ServiceCheckResultView(BaseModel):
    repo: ServiceRepoView
    incoming: list[ServiceCommitInfo] = Field(default_factory=list)
    issues: list[ServicePreflightIssue] = Field(default_factory=list)


class ServiceUpdateTaskView(BaseModel):
    task_id: str
    request_id: str = ""
    kind: Literal["check", "update"]
    state: Literal["running", "done", "failed", "interrupted"]
    stage: ServiceUpdateStage = "preflight"
    target_commit: str = ""
    created_at: datetime
    updated_at: datetime
    error: ServiceUpdateTaskError | None = None
    log_offset: int = 0
    log_truncated: bool = False
    check: ServiceCheckResultView | None = None


class ServiceUpdaterStatus(BaseModel):
    enabled: bool
    updater_ready: bool = False
    version: str = ""
    repo: ServiceRepoView = Field(default_factory=ServiceRepoView)
    runtime: ServiceRuntimeView | None = None
    preflight_issues: list[ServicePreflightIssue] = Field(default_factory=list)
    active_task: ServiceUpdateTaskView | None = None
    latest_task: ServiceUpdateTaskView | None = None


class ServiceUpdateCheckRequest(BaseModel):
    request_id: str = Field(default="", max_length=128, pattern=r"^[0-9A-Za-z._-]*$")


class ServiceUpdateApplyRequest(BaseModel):
    """提交更新：目标提交取检查时确认的远端提交，远端变化后需重新检查。"""

    target_commit: str = Field(min_length=7, max_length=40, pattern=r"^[0-9a-fA-F]+$")
    request_id: str = Field(default="", max_length=128, pattern=r"^[0-9A-Za-z._-]*$")


class ServiceUpdateTaskAccepted(BaseModel):
    task_id: str
    existing: bool = False


class ServiceUpdateTaskList(BaseModel):
    tasks: list[ServiceUpdateTaskView] = Field(default_factory=list)


class ServiceUpdateLogChunk(BaseModel):
    task_id: str
    offset: int
    total_bytes: int
    truncated: bool
    lines: list[str] = Field(default_factory=list)
