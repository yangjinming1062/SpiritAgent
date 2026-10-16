import asyncio
import contextlib
import logging
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

from spirit_agent_runner.features.browser.batch import BatchOperations
from spirit_agent_runner.features.browser.capture import CaptureOperations
from spirit_agent_runner.features.browser.connection import CdpConnection
from spirit_agent_runner.features.browser.dialog_manager import (
    DEFAULT_DIALOG_POLICY,
    DEFAULT_DIALOG_TIMEOUT_S,
    VALID_POLICIES,
    DialogBlockedError,
    DialogManager,
    DialogRecord,
    PendingDialog,
)
from spirit_agent_runner.features.browser.downloads import DownloadTracker
from spirit_agent_runner.features.browser.engine.launcher import NativeBrowserProcess
from spirit_agent_runner.features.browser.engine.selection import select_option_with_eval
from spirit_agent_runner.features.browser.engine.som import DOM_SETTLE_SCRIPT
from spirit_agent_runner.features.browser.input import InputDispatch
from spirit_agent_runner.features.browser.page_events import PageEvents
from spirit_agent_runner.features.browser.page_state import PAGE_DOMAINS, PageState
from spirit_agent_runner.features.browser.refs import Refs
from spirit_agent_runner.infrastructure.async_bridge import safe_schedule_threadsafe

logger = logging.getLogger(__name__)


class NavigationError(Exception):
    """CDP 导航返回错误。"""


@dataclass(frozen=True)
class SupervisorSnapshot:
    pending_dialogs: tuple[PendingDialog, ...]
    recent_dialogs: tuple[DialogRecord, ...]
    frame_tree: dict[str, Any]
    active: bool

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pending_dialogs": [d.to_dict() for d in self.pending_dialogs],
            "frame_tree": self.frame_tree,
        }
        if self.recent_dialogs:
            out["recent_dialogs"] = [d.to_dict() for d in self.recent_dialogs]
        return out


class CDPSupervisor:
    """Compose a CDP connection, page state and browser operations for one task."""

    def __init__(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: NativeBrowserProcess | None = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
    ) -> None:
        if dialog_policy not in VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {dialog_policy!r}")
        self.pages = PageState(self.send_cdp, lambda: self._refs.note_root_navigation())
        self.events = PageEvents()
        self.connection = CdpConnection(
            task_id,
            cdp_url,
            launch_handle=launch_handle,
            auto_owned=auto_owned,
            on_connect=self._attach_initial_page,
            handle_event=self._on_event,
            pending_dialog=lambda session_id, **kwargs: self._dialog_manager.pending_for(session_id, **kwargs),
        )
        self._dialog_manager = DialogManager(
            policy=dialog_policy,
            timeout_s=dialog_timeout_s,
            cdp_send=self.connection.send,
            loop_provider=lambda: self.connection.loop,
        )
        self._refs = Refs(
            cdp_send_async=self.connection.send,
            send_cdp_sync=self.send_cdp,
            evaluate_runtime=self.evaluate_runtime,
            loop_provider=lambda: self.connection.loop,
            session_ids_provider=self.pages.current_session,
        )
        self._input = InputDispatch(
            send_cdp=self.send_cdp,
            evaluate_runtime=self.evaluate_runtime,
            resolve_ref=self._refs.resolve_ref_center,
            session_id_provider=lambda: (sids := self.pages.current_session()).active or sids.page,
            wait_for_page_stable=self.wait_for_page_stable,
        )
        self.downloads = DownloadTracker(lambda: self.connection.stopped)
        self.capture = CaptureOperations(
            send_cdp=self.send_cdp,
            evaluate_runtime=self.evaluate_runtime,
            wait_for_page_stable=self.wait_for_page_stable,
            refs=self._refs,
            session_ids=self.pages.current_session,
        )
        self.batch = BatchOperations(self)

    def start(self, timeout: float = 15.0) -> None:
        self.connection.start(timeout)

    def stop(self) -> None:
        self.connection.stop()

    @property
    def active(self) -> bool:
        return self.connection.active

    def snapshot(self) -> SupervisorSnapshot:
        pending, recent = self._dialog_manager.snapshot()
        ft = self.pages.frame_tree()
        active = self.active

        return SupervisorSnapshot(
            pending_dialogs=pending,
            recent_dialogs=recent,
            frame_tree=ft,
            active=active,
        )

    def send_cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        loop = self.connection.loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "supervisor loop is not running"}

        sid = session_id or (sids := self.pages.current_session()).active or sids.page

        async def _do_send() -> dict[str, Any]:
            return await self.connection.send(method, params, session_id=sid, timeout=timeout)

        try:
            fut = safe_schedule_threadsafe(_do_send(), loop)
            if fut is None:
                return {"ok": False, "error": "supervisor loop unavailable"}
            res = fut.result(timeout=timeout + 2)
            return {"ok": True, "result": res["result"]}
        except DialogBlockedError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "dialog": exc.dialog.to_dict(),
                "dialog_opened_by_call": exc.opened_by_call,
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def evaluate_runtime(
        self,
        expression: str,
        *,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        res = self.send_cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if not res.get("ok"):
            return res

        result_payload = res.get("result", {})
        exception_details = result_payload.get("exceptionDetails")
        if exception_details:
            exc_text = exception_details.get("text") or "JavaScript exception"
            exc_obj = exception_details.get("exception") or {}
            desc = exc_obj.get("description")
            if desc:
                exc_text = f"{exc_text}: {desc}"
            return {"ok": False, "error": exc_text}

        result_obj = result_payload.get("result", {})
        result_type = result_obj.get("type", "undefined")
        if "value" in result_obj:
            value = result_obj["value"]
        elif result_type == "undefined":
            value = None
        else:
            value = result_obj.get("description") or result_obj.get("unserializableValue")

        return {"ok": True, "result": value, "result_type": result_type}

    def navigate(self, url: str, *, timeout: float = 30.0) -> dict[str, Any]:
        """导航到 URL，等待提交并在预算内等待网络空闲；导航本身报错时抛 NavigationError。"""
        loop = self.connection.loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Supervisor loop is not running")

        async def _do_nav() -> dict[str, Any]:
            deadline = loop.time() + timeout
            sids = self.pages.current_session()
            sid = sids.active or sids.page
            target_frame_id = sids.root_frame

            navigated_fut = self.events.await_frame_navigated(target_frame_id)
            idle_fut: asyncio.Future[dict[str, Any]] | None = None
            try:
                nav = (await self.connection.send("Page.navigate", {"url": url}, session_id=sid, timeout=timeout)).get(
                    "result",
                    {},
                )
                if nav.get("errorText"):
                    raise NavigationError(f"{nav['errorText']}: {url}")
                # 同文档 hash 跳转无 loaderId/事件。
                if loader_id := nav.get("loaderId"):
                    # 按 loaderId 等，防旧 networkIdle 放行。
                    idle_fut = self.events.await_lifecycle(loader_id, "networkIdle")
                    if target_frame_id:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(navigated_fut, timeout=max(0.0, min(20.0, deadline - loop.time())))
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(idle_fut, timeout=max(0.0, min(5.0, deadline - loop.time())))
            finally:
                navigated_fut.cancel()
                if idle_fut is not None:
                    idle_fut.cancel()

            title = ""
            with contextlib.suppress(Exception):
                title_resp = await self.connection.send(
                    "Runtime.evaluate",
                    {"expression": "document.title", "returnByValue": True},
                    session_id=sid,
                    timeout=5.0,
                )
                title = title_resp.get("result", {}).get("result", {}).get("value", "")

            final_url = url
            with contextlib.suppress(Exception):
                url_resp = await self.connection.send(
                    "Runtime.evaluate",
                    {"expression": "window.location.href", "returnByValue": True},
                    session_id=sid,
                    timeout=5.0,
                )
                final_url = url_resp.get("result", {}).get("result", {}).get("value", url)

            return {"ok": True, "frameId": target_frame_id, "url": final_url, "title": title}

        fut = safe_schedule_threadsafe(_do_nav(), loop)
        if fut is None:
            raise RuntimeError("Supervisor loop unavailable")
        try:
            # 这里只是兜底。
            return fut.result(timeout=timeout + 15)
        except TimeoutError:
            fut.cancel()
            raise TimeoutError(f"Navigation to {url} did not finish within {timeout}s") from None

    def wait_for_page_stable(self, timeout_s: float = 2.0) -> bool:
        """等待 DOM 变动沉降与页面渲染稳定。"""
        loop = self.connection.loop
        if loop is None or not loop.is_running():
            return False

        max_wait_ms = max(100, int(round(timeout_s * 1000)))
        debounce_ms = min(200, max(50, int(round(max_wait_ms / 3))))

        async def _do_wait() -> bool:
            sid = (sids := self.pages.current_session()).active or sids.page
            try:
                res = await self.connection.send(
                    "Runtime.evaluate",
                    {
                        "expression": f"({DOM_SETTLE_SCRIPT})({max_wait_ms}, {debounce_ms})",
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                    session_id=sid,
                    timeout=timeout_s + 1.0,
                )
                if res.get("result", {}).get("exceptionDetails"):
                    logger.debug("wait_for_page_stable JS exception: %s", res.get("result", {}).get("exceptionDetails"))
                    return False
                val = res.get("result", {}).get("result", {}).get("value")
                return bool(val is True)
            except Exception as exc:
                logger.debug("wait_for_page_stable error: %s", exc)
                return False

        try:
            fut = safe_schedule_threadsafe(_do_wait(), loop)
            if fut is not None:
                return fut.result(timeout=timeout_s + 1.5)
        except Exception as exc:
            logger.debug("wait_for_page_stable threadsafe wait error: %s", exc)
        return False

    def snapshot_axtree(
        self,
        *,
        interactive_only: bool = False,
        max_depth: int = 50,
    ) -> dict[str, Any]:
        """抓取 AXTree 并生成 [ref=eN] 文本快照，同步在 DOM 中注入 aria-ref 属性。"""
        return self._refs.snapshot_axtree(interactive_only=interactive_only, max_depth=max_depth)

    def click_ref(self, ref: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        return self._input.click_ref(ref, wait_stable=wait_stable, timeout_s=timeout_s)

    def type_ref(self, ref: str, text: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        """先聚焦并清空元素，再输入新文本。"""
        return self._input.type_ref(ref, text, wait_stable=wait_stable, timeout_s=timeout_s)

    def select_ref(
        self,
        ref: str,
        *,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        try:
            self._refs.require_current_ref(ref)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        return select_option_with_eval(self.evaluate_runtime, ref, value=value, label=label, index=index)

    def scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        return self._input.scroll_page(direction, pixels)

    def hover_ref(self, ref: str) -> dict[str, Any]:
        return self._input.hover_ref(ref)

    def drag_refs(self, from_ref: str, to_ref: str, *, hold_key: str | None = None, steps: int = 10) -> dict[str, Any]:
        return self._input.drag_refs(from_ref, to_ref, hold_key=hold_key, steps=steps)

    def press_key(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        return self._input.press_key(key, modifiers)

    def find_by_text(self, query: str, *, ref_only: bool = True, cap: int = 200) -> dict[str, Any]:
        return self._refs.find_by_text(query, ref_only=ref_only, cap=cap)

    def wait_for(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_s: float = 10.0,
        cancel_token: threading.Event | None = None,
    ) -> dict[str, Any]:
        return self._input.wait_for(selector=selector, text=text, timeout_s=timeout_s, cancel_token=cancel_token)

    def back(self) -> dict[str, Any]:
        sid = (sids := self.pages.current_session()).active or sids.page
        res = self.send_cdp("Page.getNavigationHistory", {}, session_id=sid)
        if not res.get("ok"):
            return res
        result = res.get("result", {})
        idx = result.get("currentIndex", 0)
        entries = result.get("entries", [])
        if idx > 0 and len(entries) > idx - 1:
            target_entry_id = entries[idx - 1]["id"]
            return self.send_cdp("Page.navigateToHistoryEntry", {"entryId": target_entry_id}, session_id=sid)
        return {"ok": False, "error": "No back history entry available"}

    def get_images(self) -> dict[str, Any]:
        return self._refs.get_images()

    async def _enable_page_domains(self, session_id: str) -> None:
        for method, params in PAGE_DOMAINS:
            await self.connection.send(method, params, session_id=session_id)

    async def _attach_initial_page(self) -> None:
        self.pages.reset_connection()
        resp = await self.connection.send("Target.getTargets")
        targets = resp.get("result", {}).get("targetInfos", [])
        attach: dict[str, Any] | None = None
        for page in targets:
            if page.get("type") != "page" or not (target_id := page.get("targetId")):
                continue
            try:
                attach = await self.connection.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            except RuntimeError as exc:
                if "No target with given id found" not in str(exc):
                    raise
                continue
            break
        if attach is None:
            try:
                created = await self.connection.send("Target.createTarget", {"url": "about:blank"})
            except RuntimeError as exc:
                if "Failed to open a new tab" not in str(exc):
                    raise
                created = await self.connection.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
            target_id = created["result"]["targetId"]
            attach = await self.connection.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        page_sid = attach["result"]["sessionId"]
        self.pages.set_session(page=page_sid, active=page_sid)
        self.pages.record_attachment(target_id, page_sid)

        sid = page_sid

        ft_resp = await self.connection.send("Page.getFrameTree", session_id=sid)
        frame = ft_resp.get("result", {}).get("frameTree", {}).get("frame", {})
        root = frame.get("id", "")
        if root:
            self.pages.record_root_frame(frame)
        else:
            self.pages.set_session(root_frame="")
        self.pages.record_attachment(target_id, page_sid, root)

        await self._enable_page_domains(sid)
        await self.connection.send("Page.bringToFront", session_id=sid)
        await self.connection.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "eventsEnabled": True, "downloadPath": tempfile.gettempdir()},
        )
        await self.connection.send(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
            session_id=sid,
        )

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Page.frameNavigated":
            self.pages.on_frame_navigated(params, session_id)
            self.events.dispatch_frame_navigated(params)
        elif method == "Page.lifecycleEvent":
            self.events.dispatch_lifecycle(params)
        elif method == "Page.javascriptDialogOpening":
            self._on_dialog_opening(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            self._dialog_manager.on_remote_closed(session_id)
        elif method == "Page.frameAttached":
            self.pages.on_frame_attached(params, session_id)
        elif method == "Page.frameDetached":
            self.pages.on_frame_detached(params)
        elif method == "Target.attachedToTarget":
            self.pages.on_target_attached(params)
        elif method == "Target.detachedFromTarget":
            self.pages.on_target_detached(params)
        elif method == "Runtime.consoleAPICalled":
            self.pages.on_console(params, level_from="api")
        elif method == "Runtime.exceptionThrown":
            self.pages.on_console(params, level_from="exception")
        elif method == "Browser.downloadWillBegin":
            self.downloads.begin(params)
        elif method == "Browser.downloadProgress":
            self.downloads.progress(params)

    def _on_dialog_opening(self, params: dict[str, Any], session_id: str | None) -> None:
        dialog = PendingDialog(
            id=self._dialog_manager.next_id(),
            type=str(params.get("type") or ""),
            message=str(params.get("message") or ""),
            default_prompt=str(params.get("defaultPrompt") or ""),
            opened_at=time.time(),
            cdp_session_id=session_id or self.pages.current_session().page or "",
            frame_id=params.get("frameId"),
        )
        self._dialog_manager.open(dialog)
        self.connection.notify_dialog_open()

    def respond_to_dialog(
        self,
        action: str,
        prompt_text: str | None = None,
        dialog_id: str | None = None,
    ) -> dict[str, Any]:
        return self._dialog_manager.respond(
            action,
            prompt_text,
            dialog_id,
            active_session_id=(sids := self.pages.current_session()).active or sids.page,
        )
