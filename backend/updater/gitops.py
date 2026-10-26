"""git 只读探测与 fast-forward 推进；不自动合并、清理或覆盖本地修改。"""

import re
from datetime import UTC, datetime

from updater.config import UpdaterSettings
from updater.contracts import CommitInfo, PreflightIssue, RepoStatus, UpdateStage
from updater.redact import redact_text
from updater.runner import LineSink, RunOutput, run_command

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class GitError(RuntimeError):
    def __init__(self, message: str, *, stage: UpdateStage = "fetch", reason: str = "git_failed") -> None:
        super().__init__(message)
        self.stage = stage
        self.reason = reason


def _git_env(settings: UpdaterSettings) -> dict[str, str]:
    return {
        "GIT_SSH_COMMAND": (
            f"ssh -i '{settings.ssh_key_path}' -o UserKnownHostsFile='{settings.ssh_known_hosts_path}'"
            " -o StrictHostKeyChecking=yes -o BatchMode=yes -o IdentitiesOnly=yes"
        ),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        # 仓库属主与容器用户不同时 git 判为可疑属主；只对本进程放开。
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "*",
        # 稳定输出，日志与解析不随用户配置漂移。
        "LC_ALL": "C",
    }


async def _git(settings: UpdaterSettings, *args: str, timeout: float | None = None) -> RunOutput:
    return await run_command(
        "git",
        "-C",
        str(settings.repo_dir),
        *args,
        timeout=timeout if timeout is not None else settings.git_timeout_seconds,
        env=_git_env(settings),
    )


async def _git_value(
    settings: UpdaterSettings,
    *args: str,
    stage: UpdateStage = "preflight",
    reason: str = "repository_failed",
    log: LineSink | None = None,
) -> str:
    """执行必须成功的 git 命令并返回输出；失败保留诊断。"""

    output = await _git(settings, *args)
    if log is not None:
        for line in (output.stdout + "\n" + output.stderr).splitlines():
            if line:
                await log(line)
    if output.code != 0:
        detail = redact_text((output.stderr or output.stdout).strip())
        raise GitError(
            f"git {args[0]} 失败：{detail or f'exit={output.code}'}",
            stage=stage,
            reason=reason,
        )
    return output.stdout.strip()


async def repo_status(settings: UpdaterSettings) -> RepoStatus:
    """只读收集仓库状态；仓库不可用按 GitError 上抛。"""

    branch = await _git_value(settings, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise GitError("仓库处于 detached HEAD 状态，无法确定要更新的分支。", stage="preflight", reason="detached_head")
    dirty = bool(await _git_value(settings, "status", "--porcelain"))
    status = RepoStatus(branch=branch, current_commit=await _git_value(settings, "rev-parse", "HEAD"), dirty=dirty)
    # 分支可能没有配置 upstream（命令非零退出），按未设置处理而不是仓库故障。
    upstream = await _git(settings, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    status.upstream = upstream.stdout.strip() if upstream.code == 0 else ""
    if not status.upstream:
        return status
    status.remote_commit = await _git_value(settings, "rev-parse", status.upstream)
    counts = (
        await _git_value(
            settings, "rev-list", "--left-right", "--count", f"{status.current_commit}...{status.remote_commit}"
        )
    ).split()
    status.ahead, status.behind = int(counts[0]), int(counts[1])
    status.diverged = status.ahead > 0 and status.behind > 0
    if status.behind:
        status.incoming = await list_commits(settings, f"{status.current_commit}..{status.remote_commit}", limit=50)
    return status


async def list_commits(settings: UpdaterSettings, revision_range: str, *, limit: int = 50) -> list[CommitInfo]:
    out = (
        await _git(
            settings,
            "log",
            "--no-color",
            f"--max-count={limit}",
            "--pretty=format:%H%x1f%h%x1f%s%x1f%an%x1f%cI",
            revision_range,
        )
    ).stdout
    commits: list[CommitInfo] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        sha, short, subject, author, stamp = parts
        try:
            committed_at = datetime.fromisoformat(stamp).astimezone(UTC)
        except ValueError:
            committed_at = None
        commits.append(
            CommitInfo(
                sha=sha,
                short_sha=short,
                subject=redact_text(subject)[:200],
                author=author[:120],
                committed_at=committed_at,
            )
        )
    return commits


def preflight_issues(status: RepoStatus) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    if not status.upstream:
        issues.append(
            PreflightIssue(check="upstream", message=f"当前分支 {status.branch} 没有 upstream，无法确定远端。")
        )
    if status.ahead > 0:
        issues.append(
            PreflightIssue(
                check="divergence",
                message=f"本地领先 upstream {status.ahead} 个提交，请先在服务器上处理（推送或重置）。",
            )
        )
    return issues


async def ssh_probe(settings: UpdaterSettings) -> tuple[bool, str]:
    """探测部署密钥能否免交互访问远端；返回 (是否正常, 末行诊断)。"""

    output = await _git(settings, "ls-remote", "--heads", "origin", timeout=30.0)
    if output.code == 0:
        return True, ""
    detail = (output.stderr or output.stdout).strip().splitlines()
    return False, detail[-1] if detail else "未知错误"


async def fetch(settings: UpdaterSettings, log: LineSink) -> RepoStatus:
    await _git_value(settings, "fetch", "--prune", "--no-tags", "origin", stage="fetch", reason="fetch_failed", log=log)
    return await repo_status(settings)


async def fast_forward(settings: UpdaterSettings, target_commit: str, log: LineSink) -> RepoStatus:
    """仅 fast-forward 推进当前分支；目标提交必须仍是当前分支 upstream 的最新提交。"""

    target = target_commit.lower()
    if not _SHA_PATTERN.fullmatch(target):
        raise GitError("目标提交号格式无效。", stage="fast_forward", reason="invalid_target")
    status = await repo_status(settings)
    if not status.upstream:
        raise GitError(f"当前分支 {status.branch} 没有 upstream。", stage="fast_forward", reason="no_upstream")
    if status.remote_commit != target:
        raise GitError(
            "远端在检查后有新变化，目标提交已不是 upstream 最新提交；请重新检查更新。",
            stage="fast_forward",
            reason="target_changed",
        )
    ancestor = await _git(settings, "merge-base", "--is-ancestor", status.current_commit, target)
    if ancestor.code != 0:
        raise GitError(
            "目标提交不能通过 fast-forward 到达；需人工处理分叉。", stage="fast_forward", reason="not_fast_forward"
        )
    # 即使已在目标提交也交给 Git 校验，避免同版本重试绕过未解决冲突检查。
    await log(f"git merge --ff-only --no-autostash --no-overwrite-ignore {target[:12]}")
    await _git_value(
        settings,
        "merge",
        "--ff-only",
        "--no-autostash",
        "--no-overwrite-ignore",
        target,
        stage="fast_forward",
        reason="merge_failed",
        log=log,
    )
    return await repo_status(settings)
