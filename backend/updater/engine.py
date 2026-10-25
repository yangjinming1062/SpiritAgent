"""检查与更新任务的执行引擎；同时只允许一个任务。"""

import asyncio
import secrets

from updater import compose, gitops
from updater.config import UpdaterSettings
from updater.contracts import (
    CheckResult,
    PreflightIssue,
    RepoStatus,
    TaskKind,
    UpdaterError,
    UpdateStage,
)
from updater.redact import redact_text
from updater.runner import LineSink, stream_command
from updater.state import StateStore, TaskRecord, utc_now

# 更新器自身不可用的预检项；脏工作区、本地领先等仓库状态问题只在页面提示。
UNAVAILABLE_CHECKS = frozenset({"repository", "docker", "compose", "ssh"})


class StageError(RuntimeError):
    """任务阶段失败；stage 缺省取当前阶段，reason 直接呈现给管理员。"""

    def __init__(self, message: str, *, reason: str, stage: UpdateStage | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.stage = stage


class TaskBusyError(RuntimeError):
    def __init__(self, active_task_id: str) -> None:
        super().__init__(f"已有任务在执行：{active_task_id}")
        self.active_task_id = active_task_id


def _preflight_failure(issues: list[PreflightIssue]) -> StageError:
    return StageError("预检未通过：" + "；".join(issue.message for issue in issues[:3]), reason="preflight_failed")


async def collect_preflight(
    settings: UpdaterSettings, log: LineSink | None = None
) -> tuple[RepoStatus, list[PreflightIssue]]:
    """读取仓库与工具链状态；log 供任务记录阶段日志，状态接口不传入。"""

    async def emit(line: str) -> None:
        if log is not None:
            await log(line)

    issues: list[PreflightIssue] = []
    try:
        status = await gitops.repo_status(settings)
    except (gitops.GitError, OSError) as exc:
        await emit(f"仓库读取失败：{exc}")
        issues.append(PreflightIssue(check="repository", message=str(exc)[:200]))
        status = RepoStatus()
    else:
        issues.extend(gitops.preflight_issues(status))
    issues.extend(await compose.toolchain_issues(settings))
    try:
        remote_ok, detail = await gitops.ssh_probe(settings)
    except OSError as exc:
        remote_ok, detail = False, str(exc)
    if remote_ok:
        await emit("SSH 远端访问正常。")
    else:
        await emit(f"SSH 探测失败：{redact_text(detail)}")
        issues.append(PreflightIssue(check="ssh", message="SSH 访问远端失败，请检查只读挂载的部署密钥。"))
    for issue in issues:
        await emit(f"预检问题：[{issue.check}] {issue.message}")
    if not issues:
        await emit("预检通过。")
    return status, issues


class TaskEngine:
    """任务在独立 asyncio task 中执行；store 负责持久化，重启时把遗留任务标为中断。"""

    def __init__(self, settings: UpdaterSettings, store: StateStore) -> None:
        self._settings = settings
        self._store = store
        # 事件循环只保留任务的弱引用，必须自己持有引用直到结束。
        self._running: set[asyncio.Task[None]] = set()

    async def start(self, kind: TaskKind, *, request_id: str, target_commit: str = "") -> tuple[TaskRecord, bool]:
        """提交任务；同一 request_id 重复提交返回原任务，已有任务时拒绝。"""

        if request_id and (existing := self._store.find_by_request(request_id)) is not None:
            return existing, True
        if (active := self._store.active) is not None:
            raise TaskBusyError(active.task_id)
        task_id = secrets.token_hex(12)
        record = TaskRecord(
            task_id=task_id,
            request_id=request_id,
            kind=kind,
            target_commit=target_commit,
            log=self._store.log_for(task_id),
        )
        await self._store.begin(record)
        task = asyncio.create_task(self._run(record))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return record, False

    async def _run(self, record: TaskRecord) -> None:
        try:
            if record.kind == "check":
                await self._run_check(record)
            else:
                await self._run_update(record)
            record.state = "done"
            record.stage = "done"
        except (StageError, gitops.GitError) as exc:
            await record.log.append(f"失败：{exc}")
            record.error = UpdaterError(stage=exc.stage or record.stage, reason=exc.reason, message=str(exc)[:400])
            record.state = "failed"
        except TimeoutError:
            await record.log.append("阶段超时，已终止子进程并按失败收尾。")
            record.error = UpdaterError(stage=record.stage, reason="timeout", message="阶段超时。")
            record.state = "failed"
        except Exception as exc:  # noqa: BLE001 - 任务边界必须收敛所有异常并保留诊断
            await record.log.append(f"任务异常终止：{exc!r}")
            record.error = UpdaterError(stage=record.stage, reason="internal_error", message=str(exc)[:400])
            record.state = "failed"
        finally:
            record.updated_at = utc_now()
            await self._store.finish()

    def _stage(self, record: TaskRecord, stage: UpdateStage) -> None:
        record.stage = stage
        record.updated_at = utc_now()

    async def _preflight(self, record: TaskRecord) -> tuple[RepoStatus, list[PreflightIssue]]:
        self._stage(record, "preflight")
        await record.log.append("== 预检：仓库 / SSH / Docker / Compose ==")
        return await collect_preflight(self._settings, record.log.append)

    async def _run_check(self, record: TaskRecord) -> None:
        status, issues = await self._preflight(record)
        record.check = CheckResult(repo=status, incoming=[], issues=issues)
        if any(issue.check == "repository" for issue in issues):
            # 只有仓库不可读会中断检查；工具链与 SSH 问题仍可读取远端提交。
            raise _preflight_failure(issues)
        self._stage(record, "fetch")
        await record.log.append("== 获取 upstream 最新提交 ==")
        status = await gitops.fetch(self._settings)
        await record.log.append(f"分支 {status.branch}：落后 {status.behind} 个提交。")
        record.check = CheckResult(repo=status, incoming=status.incoming, issues=issues)

    async def _run_update(self, record: TaskRecord) -> None:
        _, issues = await self._preflight(record)
        if issues:
            raise _preflight_failure(issues)
        self._stage(record, "fetch")
        await record.log.append("== 获取 upstream 最新提交 ==")
        await gitops.fetch(self._settings)
        self._stage(record, "fast_forward")
        await record.log.append("== fast-forward 推进服务器分支 ==")
        commit = (await gitops.fast_forward(self._settings, record.target_commit, record.log.append)).current_commit
        await record.log.append(f"仓库版本：{commit[:12]}")
        self._stage(record, "build")
        await record.log.append(f"== 构建目标服务：{' '.join(compose.SERVICE_NAMES)} ==")
        await self._compose(
            record,
            "build",
            "--build-arg",
            f"GIT_COMMIT={commit}",
            *compose.SERVICE_NAMES,
            timeout=self._settings.build_timeout_seconds,
        )
        self._stage(record, "restart")
        await record.log.append("== 重启目标服务并等待健康 ==")
        await self._compose(
            record,
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--wait",
            "--wait-timeout",
            str(int(self._settings.start_timeout_seconds)),
            *compose.SERVICE_NAMES,
            timeout=self._settings.start_timeout_seconds + 60.0,
        )
        self._stage(record, "verify")
        await record.log.append("== 核对服务健康与运行镜像 ==")
        try:
            runtime = await compose.read_backend(self._settings)
        except (RuntimeError, ValueError, OSError) as exc:
            raise StageError(f"读取运行状态失败：{exc}", reason="verify_failed") from exc
        await record.log.append(
            f"backend: state={runtime.state} health={runtime.health} image_commit={runtime.running_commit or '未知'}"
        )
        if runtime.running_commit and runtime.running_commit != commit:
            raise StageError(
                f"backend 运行镜像({runtime.running_commit[:12]})与仓库版本({commit[:12]})不一致。",
                reason="runtime_mismatch",
            )
        await record.log.append("更新完成。")

    async def _compose(self, record: TaskRecord, *args: str, timeout: float) -> None:
        code = await stream_command(
            "docker",
            "compose",
            *args,
            timeout=timeout,
            on_line=record.log.append,
            cwd=compose.compose_cwd(self._settings),
        )
        if code != 0:
            raise StageError(f"docker compose {args[0]} 退出码 {code}，详见任务日志。", reason="command_failed")
