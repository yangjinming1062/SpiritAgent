"""检查与更新任务的执行引擎；同时只允许一个任务。"""

import asyncio
import logging
import secrets
from collections.abc import Coroutine
from typing import Any

from infrastructure.async_io import settle_task
from infrastructure.updater_contracts import (
    ServiceCheckResultView,
    ServicePreflightIssue,
    ServiceRepoView,
    ServiceUpdateStage,
    ServiceUpdateTaskError,
    ServiceUpdateTaskView,
    TaskKind,
)
from updater import compose, gitops
from updater.config import UpdaterSettings
from updater.redact import redact_text
from updater.runner import LineSink, stream_command
from updater.state import StateStore, TaskRecord, utc_now

# 更新器自身不可用的预检项；本地领先等仓库状态问题只在页面提示。
UNAVAILABLE_CHECKS = frozenset({"repository", "docker", "compose", "ssh"})


class StageError(RuntimeError):
    """任务阶段失败；stage 缺省取当前阶段，reason 直接呈现给管理员。"""

    def __init__(self, message: str, *, reason: str, stage: ServiceUpdateStage | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.stage = stage


def _preflight_failure(issues: list[ServicePreflightIssue]) -> StageError:
    return StageError("预检未通过：" + "；".join(issue.message for issue in issues[:3]), reason="preflight_failed")


async def collect_preflight(
    settings: UpdaterSettings, log: LineSink | None = None
) -> tuple[ServiceRepoView, list[ServicePreflightIssue]]:
    """读取仓库与工具链状态；log 供任务记录阶段日志，状态接口不传入。"""

    async def emit(line: str) -> None:
        if log is not None:
            await log(line)

    issues: list[ServicePreflightIssue] = []
    try:
        status = await gitops.repo_status(settings)
    except (gitops.GitError, OSError) as exc:
        await emit(f"仓库读取失败：{exc}")
        issues.append(ServicePreflightIssue(check="repository", message=str(exc)[:200]))
        status = ServiceRepoView()
    else:
        issues.extend(gitops.preflight_issues(status))
        if status.dirty:
            await emit("工作区有未提交修改；推进时由 Git 检查是否可安全保留。")
    issues.extend(await compose.toolchain_issues(settings))
    try:
        remote_ok, detail = await gitops.ssh_probe(settings)
    except OSError as exc:
        remote_ok, detail = False, str(exc)
    if remote_ok:
        await emit("SSH 远端访问正常。")
    else:
        await emit(f"SSH 探测失败：{redact_text(detail)}")
        issues.append(ServicePreflightIssue(check="ssh", message="SSH 访问远端失败，请检查只读挂载的部署密钥。"))
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
        self._running: set[asyncio.Task] = set()

    def _track[T](self, operation: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.create_task(operation)
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task

    async def start(self, kind: TaskKind, *, request_id: str, target_commit: str = "") -> tuple[TaskRecord, bool]:
        """提交任务；同一 request_id 重复提交返回原任务，已有任务时拒绝。"""

        now = utc_now()
        record = self._store.record(
            ServiceUpdateTaskView(
                task_id=secrets.token_hex(12),
                request_id=request_id,
                kind=kind,
                state="running",
                target_commit=target_commit,
                created_at=now,
                updated_at=now,
            )
        )
        # 请求连接断开时，已开始的登记仍须在落盘后派发。
        return await asyncio.shield(self._track(self._admit(record)))

    async def _admit(self, record: TaskRecord) -> tuple[TaskRecord, bool]:
        record, existing = await self._store.begin(record)
        if not existing:
            self._track(self._run(record))
        return record, existing

    async def _run(self, record: TaskRecord) -> None:
        try:
            if record.task.kind == "check":
                await self._run_check(record)
            else:
                await self._run_update(record)
            record.task.state = "done"
            record.task.stage = "done"
        except asyncio.CancelledError:
            record.task.state = "interrupted"
            raise
        except Exception as exc:
            if isinstance(exc, (StageError, gitops.GitError)):
                reason, stage, message = exc.reason, exc.stage or record.task.stage, str(exc)
            elif isinstance(exc, TimeoutError):
                reason, stage, message = "timeout", record.task.stage, "阶段超时。"
            else:
                reason, stage, message = "internal_error", record.task.stage, str(exc)
            record.task.state = "failed"
            record.task.error = ServiceUpdateTaskError(stage=stage, reason=reason, message=redact_text(message)[:400])
            try:
                await record.log.append(f"失败：{record.task.error.message}")
            except OSError:
                logging.getLogger(__name__).warning("更新器无法写入失败日志")
        finally:
            record.task.updated_at = utc_now()
            await settle_task(asyncio.create_task(self._store.finish()))

    async def _stage(self, record: TaskRecord, stage: ServiceUpdateStage) -> None:
        record.task.stage = stage
        record.task.updated_at = utc_now()
        await self._store.checkpoint()

    async def _preflight(self, record: TaskRecord) -> tuple[ServiceRepoView, list[ServicePreflightIssue]]:
        await self._stage(record, "preflight")
        await record.log.append("== 预检：仓库 / SSH / Docker / Compose ==")
        return await collect_preflight(self._settings, record.log.append)

    async def _run_check(self, record: TaskRecord) -> None:
        status, issues = await self._preflight(record)
        record.task.check = ServiceCheckResultView(repo=status, incoming=[], issues=issues)
        if any(issue.check == "repository" for issue in issues):
            # 只有仓库不可读会中断检查；工具链与 SSH 问题仍可读取远端提交。
            raise _preflight_failure(issues)
        await self._stage(record, "fetch")
        await record.log.append("== 获取 upstream 最新提交 ==")
        status = await gitops.fetch(self._settings, record.log.append)
        await record.log.append(f"分支 {status.branch}：落后 {status.behind} 个提交。")
        incoming = (
            await gitops.list_commits(self._settings, f"{status.current_commit}..{status.remote_commit}")
            if status.behind
            else []
        )
        record.task.check = ServiceCheckResultView(repo=status, incoming=incoming, issues=issues)

    async def _run_update(self, record: TaskRecord) -> None:
        _, issues = await self._preflight(record)
        if issues:
            raise _preflight_failure(issues)
        await self._stage(record, "fetch")
        await record.log.append("== 获取 upstream 最新提交 ==")
        await gitops.fetch(self._settings, record.log.append)
        await self._stage(record, "fast_forward")
        await record.log.append("== fast-forward 推进服务器分支 ==")
        commit = (
            await gitops.fast_forward(self._settings, record.task.target_commit, record.log.append)
        ).current_commit
        await record.log.append(f"仓库版本：{commit[:12]}")
        await self._stage(record, "build")
        await record.log.append(f"== 构建目标服务：{' '.join(compose.SERVICE_NAMES)} ==")
        await self._compose(
            record,
            "build",
            "--build-arg",
            f"GIT_COMMIT={commit}",
            *compose.SERVICE_NAMES,
            timeout=self._settings.build_timeout_seconds,
        )
        await self._stage(record, "restart")
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
        await self._stage(record, "verify")
        await record.log.append("== 核对服务健康与运行镜像 ==")
        try:
            runtime = await compose.read_backend(self._settings)
        except (RuntimeError, ValueError, OSError) as exc:
            raise StageError(f"读取运行状态失败：{exc}", reason="verify_failed") from exc
        await record.log.append(
            f"backend: state={runtime.state} health={runtime.health} image_commit={runtime.running_commit or '未知'}"
        )
        if runtime.state != "running" or runtime.health != "healthy" or not runtime.running_commit:
            raise StageError("无法确认 backend 正在健康运行并取得运行镜像的提交标识。", reason="verify_failed")
        if runtime.running_commit != commit:
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
