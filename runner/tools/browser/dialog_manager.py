"""JS 弹窗策略调度与生命周期；_lock 保护 pending/recent/watchdogs/seq，工作在 supervisor loop 线程。"""

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from utils import safe_schedule_threadsafe

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


class DialogBlockedError(Exception):
    """待决弹窗阻塞页面；opened_by_call 区分调用内触发（已生效）与调用前已有（未执行）。"""

    def __init__(self, dialog: PendingDialog, *, opened_by_call: bool) -> None:
        self.dialog = dialog
        self.opened_by_call = opened_by_call
        super().__init__(
            f"The page is blocked by a JavaScript {dialog.type} dialog (dialog_id={dialog.id}, "
            f"message={dialog.message!r}). Respond to it with browser_dialog before continuing.",
        )


CdpSendFn = Callable[..., Awaitable[dict[str, Any]]]
LoopProvider = Callable[[], asyncio.AbstractEventLoop | None]


class DialogManager:
    """弹窗生命周期管理：open / on_remote_closed / respond / 自动策略 / 看门狗。"""

    def __init__(
        self,
        *,
        policy: str,
        timeout_s: float,
        cdp_send: CdpSendFn,
        loop_provider: LoopProvider,
    ) -> None:
        if policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {policy!r}")
        self._policy = policy
        self._timeout_s = float(timeout_s)
        self._cdp_send = cdp_send
        self._loop_provider = loop_provider

        self._lock = threading.Lock()
        self._pending: dict[str, PendingDialog] = {}
        self._recent: list[DialogRecord] = []
        self._watchdogs: dict[str, asyncio.TimerHandle] = {}
        self._seq = 0
        # 持有任务引用到完成；看门狗随 loop 失效。
        self._tasks: set[asyncio.Task[bool]] = set()

    def next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"d-{self._seq}"

    def open(self, dialog: PendingDialog) -> None:
        """在 loop 线程处理新弹窗：自动策略立即应答，must_respond 挂起等待模型并启动看门狗。"""
        if self._policy in (DIALOG_POLICY_AUTO_DISMISS, DIALOG_POLICY_AUTO_ACCEPT):
            with self._lock:
                self._archive_locked(dialog, "auto_policy")
            accept = self._policy == DIALOG_POLICY_AUTO_ACCEPT
            self._schedule_fulfill(dialog, accept=accept, prompt_text=dialog.default_prompt if accept else "")
            return
        if self._timeout_s > 0:
            dialog.deadline = time.time() + self._timeout_s
        with self._lock:
            self._pending[dialog.id] = dialog
            if self._timeout_s > 0:
                self._watchdogs[dialog.id] = asyncio.get_running_loop().call_later(
                    self._timeout_s,
                    self._expire_watchdog,
                    dialog,
                )

    def on_remote_closed(self, session_id: str | None) -> None:
        """关闭同 session 待决弹窗；session_id 为 None 时匹配任意待决。"""
        dialog = self.pending_for(session_id)
        if dialog is None:
            return
        with self._lock:
            if self._pending.pop(dialog.id, None) is None:
                return
            self._archive_locked(dialog, "remote")
            handle = self._watchdogs.pop(dialog.id, None)
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
                matched = [
                    d for d in self._pending.values() if active_session_id and d.cdp_session_id == active_session_id
                ]
                dialog = matched[0] if matched else next(iter(self._pending.values()))

        accept = action == "accept"
        pt = prompt_text or ""

        async def _do_respond() -> dict[str, Any]:
            # loop 线程内校验，避免与看门狗双应答。
            with self._lock:
                if self._pending.pop(dialog.id, None) is None:
                    return {"ok": False, "error": "Dialog already handled (expired or removed)"}
                self._archive_locked(dialog, "agent")
                handle = self._watchdogs.pop(dialog.id, None)
            if handle is not None:
                handle.cancel()
            # 投递失败须如实上报。
            if not await self._fulfill(dialog, accept=accept, prompt_text=pt):
                return {
                    "ok": False,
                    "error": "dialog response not delivered; the page may still be blocked",
                    "dialog": dialog.to_dict(),
                }
            return {"ok": True, "dialog": dialog.to_dict()}

        loop = self._loop_provider()
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "Supervisor loop is not running"}
        fut = safe_schedule_threadsafe(_do_respond(), loop)
        if fut is None:
            return {"ok": False, "error": "Supervisor loop is not running"}
        try:
            return fut.result(timeout=10.0)
        except Exception as exc:
            fut.cancel()
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def pending_for(self, session_id: str | None, *, exclude_id: str | None = None) -> PendingDialog | None:
        """返回阻塞该会话的待决弹窗（跳过 exclude_id）；session_id 为 None 时匹配任一会话。"""
        with self._lock:
            return next(
                (
                    d
                    for d in self._pending.values()
                    if d.id != exclude_id and (session_id is None or d.cdp_session_id == session_id)
                ),
                None,
            )

    def snapshot(self) -> tuple[tuple[PendingDialog, ...], tuple[DialogRecord, ...]]:
        """原子导出 pending + recent 副本，不与外部状态锁嵌套。"""
        with self._lock:
            return tuple(self._pending.values()), tuple(self._recent)

    async def _fulfill(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> bool:
        params: dict[str, Any] = {"accept": accept}
        if dialog.type == "prompt":
            params["promptText"] = prompt_text
        try:
            await self._cdp_send(
                "Page.handleJavaScriptDialog",
                params,
                session_id=dialog.cdp_session_id or None,
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning("Page.handleJavaScriptDialog failed for %s: %s", dialog.id, exc)
            return False
        return True

    def _schedule_fulfill(self, dialog: PendingDialog, *, accept: bool, prompt_text: str) -> None:
        task = asyncio.get_running_loop().create_task(self._fulfill(dialog, accept=accept, prompt_text=prompt_text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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

    def _expire_watchdog(self, dialog: PendingDialog) -> None:
        with self._lock:
            if self._pending.pop(dialog.id, None) is None:
                return
            self._archive_locked(dialog, "timeout")
            self._watchdogs.pop(dialog.id, None)
        self._schedule_fulfill(dialog, accept=False, prompt_text="")
