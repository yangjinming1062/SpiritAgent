"""JS-dialog 和 Fetch-bridge-dialog 的统一策略调度与生命周期管理。

锁 #2：DialogManager._lock（pending/recent/watchdogs/seq），_BgTaskBag._lock 锁 #3。
"""

import asyncio
import base64
import contextlib
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


DIALOG_POLICY_MUST_RESPOND = "must_respond"
DIALOG_POLICY_AUTO_DISMISS = "auto_dismiss"
DIALOG_POLICY_AUTO_ACCEPT = "auto_accept"

_VALID_POLICIES = frozenset({DIALOG_POLICY_MUST_RESPOND, DIALOG_POLICY_AUTO_DISMISS, DIALOG_POLICY_AUTO_ACCEPT})

DEFAULT_DIALOG_POLICY = DIALOG_POLICY_MUST_RESPOND
DEFAULT_DIALOG_TIMEOUT_S = 300.0

RECENT_DIALOGS_MAX = 20


@dataclass
class PendingDialog:
    id: str
    type: str
    message: str
    default_prompt: str
    opened_at: float
    cdp_session_id: str
    frame_id: str | None = None
    bridge_request_id: str | None = None
    deadline: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "default_prompt": self.default_prompt,
            "opened_at": self.opened_at,
            "frame_id": self.frame_id,
        }
        if self.deadline is not None:
            out["deadline"] = self.deadline
        return out


@dataclass
class DialogRecord:
    id: str
    type: str
    message: str
    opened_at: float
    closed_at: float
    closed_by: str
    frame_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "message": self.message,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
            "frame_id": self.frame_id,
        }


CdpSendFn = Callable[..., Awaitable[dict[str, Any]]]


class DialogDispatchStrategy(Protocol):
    """弹窗响应的策略接口。fulfill 返回 bool 表示 CDP 应答是否成功，
    schedule_* 把应答作为后台 task 排队并返回该 task 以便追踪。"""

    async def fulfill(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> bool: ...

    def schedule_dismiss(self, dialog: PendingDialog) -> asyncio.Task: ...

    def schedule_accept(self, dialog: PendingDialog, default_prompt: str) -> asyncio.Task: ...


class _BgTaskBag:
    """异步后台任务集合，提供 stop() 时的 cancel-and-await 安全收尾。

    锁 #3。add() 入队后挂 done_callback 自我注销；drain() 在 loop 关闭前
    先 cancel 所有未完成任务，再 await gather，使被取消协程有干净退出窗口。
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Future] = set()
        self._lock = threading.Lock()

    def add(self, fut: asyncio.Future) -> None:
        with self._lock:
            self._tasks.add(fut)
        fut.add_done_callback(self._discard)

    def _discard(self, fut: asyncio.Future) -> None:
        with self._lock:
            self._tasks.discard(fut)

    def drain(self, loop: asyncio.AbstractEventLoop, timeout: float = 2.0) -> None:
        with self._lock:
            pending = [f for f in self._tasks if not f.done()]
        if not pending:
            return
        for fut in pending:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(fut.cancel)

        async def _await_all() -> None:
            await asyncio.gather(*pending, return_exceptions=True)

        gather_fut = asyncio.run_coroutine_threadsafe(_await_all(), loop)
        with contextlib.suppress(Exception):
            gather_fut.result(timeout=timeout)


class JSDialogStrategy:
    """Page.handleJavaScriptDialog 弹窗（window.alert/confirm/prompt）。"""

    def __init__(self, cdp_send: CdpSendFn, bg: _BgTaskBag) -> None:
        self._cdp_send = cdp_send
        self._bg = bg

    async def fulfill(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> bool:
        params: dict[str, Any] = {"accept": accept}
        if dialog.type == "prompt":
            params["promptText"] = prompt_text
        try:
            res = await self._cdp_send(
                "Page.handleJavaScriptDialog",
                params,
                session_id=dialog.cdp_session_id or None,
                timeout=5.0,
            )
            return isinstance(res, dict) and "error" not in res
        except Exception:
            return False

    def schedule_dismiss(self, dialog: PendingDialog) -> asyncio.Task:
        return asyncio.create_task(self.fulfill(dialog, accept=False, prompt_text=""))

    def schedule_accept(self, dialog: PendingDialog, default_prompt: str) -> asyncio.Task:
        return asyncio.create_task(self.fulfill(dialog, accept=True, prompt_text=default_prompt))


class BridgeDialogStrategy:
    """Fetch.requestPaused 转发的桥接弹窗（页面端用 XHR 同步等待）。"""

    def __init__(self, cdp_send: CdpSendFn, bg: _BgTaskBag) -> None:
        self._cdp_send = cdp_send
        self._bg = bg

    async def fulfill(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> bool:
        if not dialog.bridge_request_id:
            return False
        body = json.dumps({"accept": accept, "prompt_text": prompt_text})
        body_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        try:
            res = await self._cdp_send(
                "Fetch.fulfillRequest",
                {
                    "requestId": dialog.bridge_request_id,
                    "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": body_b64,
                },
                session_id=dialog.cdp_session_id or None,
                timeout=5.0,
            )
            return isinstance(res, dict) and "error" not in res
        except Exception:
            return False

    def schedule_dismiss(self, dialog: PendingDialog) -> asyncio.Task:
        return asyncio.create_task(self.fulfill(dialog, accept=False, prompt_text=""))

    def schedule_accept(self, dialog: PendingDialog, default_prompt: str) -> asyncio.Task:
        return asyncio.create_task(self.fulfill(dialog, accept=True, prompt_text=default_prompt))


SessionIdProvider = Callable[[], str | None]
LoopProvider = Callable[[], asyncio.AbstractEventLoop | None]


def _strategy_for(dialog: PendingDialog, js: JSDialogStrategy, bridge: BridgeDialogStrategy) -> DialogDispatchStrategy:
    return bridge if dialog.bridge_request_id else js


class DialogManager:
    """弹窗生命周期管理：open / on_remote_closed / respond / 主动响应 / 看门狗。

    单一锁 DialogManager._lock 覆盖 pending / recent / watchdogs / seq 全部字段；
    bg 任务集合由 _BgTaskBag 单独加锁（锁 #3）。
    """

    def __init__(
        self,
        *,
        policy: str,
        timeout_s: float,
        cdp_send: CdpSendFn,
        loop_provider: LoopProvider,
        session_id_provider: SessionIdProvider,
    ) -> None:
        if policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {policy!r}")
        self._policy = policy
        self._timeout_s = float(timeout_s)
        self._cdp_send = cdp_send
        self._loop_provider = loop_provider
        self._session_id_provider = session_id_provider

        self._lock = threading.Lock()  # 锁 #2
        self._pending: dict[str, PendingDialog] = {}
        self._recent: list[DialogRecord] = []
        self._watchdogs: dict[str, asyncio.TimerHandle] = {}
        self._seq = 0
        self._bg = _BgTaskBag()

        self._js = JSDialogStrategy(cdp_send, self._bg)
        self._bridge = BridgeDialogStrategy(cdp_send, self._bg)

    @property
    def bg(self) -> _BgTaskBag:
        return self._bg

    def next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"d-{self._seq}"

    def open(self, dialog: PendingDialog) -> None:
        """统一弹窗入口：按 dialog_policy 分派到 auto / must_respond 两条路径。"""
        strategy = _strategy_for(dialog, self._js, self._bridge)
        if self._policy == DIALOG_POLICY_AUTO_DISMISS:
            with self._lock:
                self._archive_locked(dialog, "auto_policy")
            task = strategy.schedule_dismiss(dialog)
            self._bg.add(task)
            self._install_auto_retry(dialog, strategy, task, accept=False, prompt_text="")
        elif self._policy == DIALOG_POLICY_AUTO_ACCEPT:
            with self._lock:
                self._archive_locked(dialog, "auto_policy")
            task = strategy.schedule_accept(dialog, dialog.default_prompt)
            self._bg.add(task)
            self._install_auto_retry(dialog, strategy, task, accept=True, prompt_text=dialog.default_prompt)
        else:
            with self._lock:
                self._pending[dialog.id] = dialog
            dialog.deadline = time.time() + self._timeout_s if self._timeout_s > 0 else None
            self._install_watchdog(dialog)

    def _install_auto_retry(
        self,
        dialog: PendingDialog,
        strategy: DialogDispatchStrategy,
        first_task: asyncio.Task,
        *,
        accept: bool,
        prompt_text: str,
    ) -> None:
        """bridge dialog 自动策略兜底：仅当首次 fulfill 失败（CDP 抖动 / requestId 过期）时
        1.5s 后重试一次；首次成功则跳过，避免对已 resume 的 request 重复发 CDP 触发
        浏览器侧 Invalid requestId。JS dialog 不需要 retry（CDP 失败就是真的失败）。"""
        if not dialog.bridge_request_id:
            return

        async def _retry_after_delay() -> None:
            await asyncio.sleep(1.5)
            with contextlib.suppress(Exception):
                await strategy.fulfill(dialog, accept=accept, prompt_text=prompt_text)

        def _maybe_retry(t: asyncio.Task) -> None:
            try:
                succeeded = t.exception() is None and bool(t.result())
            except Exception:
                succeeded = False
            if succeeded:
                return
            try:
                retry_task = asyncio.create_task(_retry_after_delay())
            except RuntimeError:
                return
            self._bg.add(retry_task)

        first_task.add_done_callback(_maybe_retry)

    def on_remote_closed(self, session_id: str | None) -> None:
        """处理 Page.javascriptDialogClosed 事件，关闭同 session 的 JS 弹窗（非 bridge）。

        session_id 为 None 时（顶层 CDP 事件 / oopif 边缘情况）：回退为匹配任意无 bridge_request_id 的 pending JS dialog。
        """
        with self._lock:
            if session_id is None:
                candidates = [d.id for d in self._pending.values() if d.bridge_request_id is None]
            else:
                candidates = [
                    d.id
                    for d in self._pending.values()
                    if d.cdp_session_id == session_id and d.bridge_request_id is None
                ]
            if not candidates:
                return
            did = candidates[0]
            d = self._pending.pop(did, None)
            if d is not None:
                self._archive_locked(d, "remote")
            handle = self._watchdogs.pop(did, None)
        if handle is not None:
            handle.cancel()

    def respond(
        self,
        action: str,
        prompt_text: str | None,
        dialog_id: str | None,
        *,
        active_session_id: str | None,
    ) -> dict[str, Any]:
        """模型主动应答弹窗。无 dialog_id 时优先匹配当前 active session。"""
        with self._lock:
            if not self._pending:
                return {"ok": False, "error": "No pending dialog to respond to."}
            if dialog_id:
                dialog = self._pending.get(dialog_id)
                if not dialog:
                    return {"ok": False, "error": f"Dialog {dialog_id} not found."}
            else:
                matched = (
                    [d for d in self._pending.values() if d.cdp_session_id == active_session_id]
                    if active_session_id
                    else []
                )
                dialog = matched[0] if matched else next(iter(self._pending.values()))

        accept = action == "accept"
        pt = prompt_text or ""
        strategy = _strategy_for(dialog, self._js, self._bridge)

        async def _do_respond() -> dict[str, Any]:
            # 先在 loop 线程内重新校验 + 弹出 + 取消看门狗，再发 fulfill，
            # 避免与 _expire_watchdog 竞争导致双方各发一次 CDP 应答。
            with self._lock:
                if dialog.id not in self._pending:
                    return {"ok": False, "error": "Dialog already handled (expired or removed)"}
                self._pending.pop(dialog.id, None)
                self._archive_locked(dialog, "agent")
                handle = self._watchdogs.pop(dialog.id, None)
            if handle is not None:
                handle.cancel()
            await strategy.fulfill(dialog, accept=accept, prompt_text=pt)
            return {"ok": True, "dialog": dialog.to_dict()}

        loop = self._loop_provider()
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}
        from utils import safe_schedule_threadsafe  # late import: avoid utils at module load

        try:
            fut = safe_schedule_threadsafe(_do_respond(), loop)
            if fut is None:
                return {"ok": False, "error": "supervisor loop unavailable"}
            return fut.result(timeout=10.0)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def snapshot(self) -> tuple[tuple[PendingDialog, ...], tuple[DialogRecord, ...]]:
        """原子导出 pending + recent 副本，不与外部状态锁嵌套。"""
        with self._lock:
            return tuple(self._pending.values()), tuple(self._recent)

    def shutdown(self) -> None:
        """stop() 阶段调用：取消所有看门狗，对未决 bridge dialog 同步等待 fulfill 完成
        （避免 drain() 立即取消尚未发出的 fulfill 协程，让页面 XHR 永久卡死）。
        同时把 bridge dialog 从 _pending 弹出并归档为 timeout，snapshot 不会再报 stale 项。

        bridge dialog 的 Fetch.fulfillRequest 必须真的到达浏览器才能 resume XHR；
        直接通过 run_coroutine_threadsafe 调度到 loop 并同步等待，drain() 后续再
        清空剩余的常规 bg 任务。
        """
        with self._lock:
            handles = list(self._watchdogs.values())
            self._watchdogs.clear()
            bridge_dialogs = [d for d in self._pending.values() if d.bridge_request_id]
            for d in bridge_dialogs:
                self._pending.pop(d.id, None)
                self._archive_locked(d, "timeout")
        for h in handles:
            with contextlib.suppress(Exception):
                h.cancel()
        loop = self._loop_provider()
        if loop is None or not loop.is_running():
            return
        # 并行调度所有 bridge fulfill（每个都独立 target 一个 requestId），避免
        # N 个 bridge dialog 串行等待 N×3s 的 worst-case shutdown 阻塞。
        # 先提交再统一等结果：loop 端 CDP 调度可并发，调用线程仅同步等 max 而非 sum。
        futs = [
            asyncio.run_coroutine_threadsafe(self._bridge.fulfill(d, accept=False, prompt_text=""), loop)
            for d in bridge_dialogs
        ]
        for fut in futs:
            with contextlib.suppress(Exception):
                fut.result(timeout=3.0)

    def _archive_locked(self, dialog: PendingDialog, closed_by: str) -> None:
        record = DialogRecord(
            id=dialog.id,
            type=dialog.type,
            message=dialog.message,
            opened_at=dialog.opened_at,
            closed_at=time.time(),
            closed_by=closed_by,
            frame_id=dialog.frame_id,
        )
        self._recent.append(record)
        if len(self._recent) > RECENT_DIALOGS_MAX * 2:
            self._recent = self._recent[-RECENT_DIALOGS_MAX:]

    def _install_watchdog(self, dialog: PendingDialog) -> None:
        if self._timeout_s <= 0:
            return
        loop = self._loop_provider()
        if loop is None:
            return
        try:
            handle = loop.call_later(self._timeout_s, self._expire_watchdog, dialog)
        except RuntimeError:
            return
        with self._lock:
            self._watchdogs[dialog.id] = handle

    def _expire_watchdog(self, dialog: PendingDialog) -> None:
        with self._lock:
            if dialog.id not in self._pending:
                return
            self._pending.pop(dialog.id, None)
            self._archive_locked(dialog, "timeout")
            handle = self._watchdogs.pop(dialog.id, None)
        if handle is not None:
            handle.cancel()
        strategy = _strategy_for(dialog, self._js, self._bridge)
        # 与 open() 的 auto 路径一致：把 schedule_dismiss 返回的 task 加入 bg 集合，
        # 让 stop() 的 drain 能取消未完成的 dismiss CDP 调用，避免关 loop 时中途 race。
        try:
            dismiss_task = strategy.schedule_dismiss(dialog)
            self._bg.add(dismiss_task)
        except RuntimeError:
            pass
