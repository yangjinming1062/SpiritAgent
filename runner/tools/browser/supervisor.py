import asyncio
import base64
import contextlib
import enum
import json
import logging
import random
import tempfile
import threading
import time
import uuid
from _thread import RLock
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import websockets
from utils import safe_schedule_threadsafe

from .dialog_manager import (
    _VALID_POLICIES,
    DEFAULT_DIALOG_POLICY,
    DEFAULT_DIALOG_TIMEOUT_S,
    DialogBlockedError,
    DialogManager,
    DialogRecord,
    PendingDialog,
)
from .engine import (
    DOM_SETTLE_SCRIPT,
    SOM_INJECT_SCRIPT,
    SOM_REMOVE_SCRIPT,
    format_som_annotation_context,
    parse_som_results,
    select_option_with_eval,
)
from .engine.launcher import NativeBrowserProcess
from .input import InputDispatch, parse_numeric_unit
from .refs import Refs, SessionIds

logger = logging.getLogger(__name__)

_CDP_BACKOFF_MAX = 10.0
# 弹窗阻塞渲染进程命令，超宽限判 blocked。
_DIALOG_BLOCK_GRACE_S = 1.0
# 导航会关旧弹窗，不判阻塞。
_NAVIGATION_METHODS = frozenset({"Page.navigate", "Page.navigateToHistoryEntry", "Page.reload"})


class _Unset(enum.Enum):
    UNSET = enum.auto()


_UNSET: Final = _Unset.UNSET

CONSOLE_HISTORY_MAX = 50

# page 会话须启用的事件域。
_PAGE_DOMAINS: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("Page.enable", None),
    ("Page.setLifecycleEventsEnabled", {"enabled": True}),
    ("Runtime.enable", None),
    ("Accessibility.enable", None),
    ("DOM.enable", None),
)


class NavigationError(Exception):
    """CDP 导航返回错误。"""


@dataclass
class FrameInfo:
    frame_id: str
    url: str
    origin: str
    parent_frame_id: str | None
    is_oopif: bool
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return (
            {"frame_id": self.frame_id, "url": self.url, "origin": self.origin, "is_oopif": self.is_oopif}
            | ({"parent_frame_id": self.parent_frame_id} if self.parent_frame_id else {})
            | ({"name": self.name} if self.name else {})
        )


@dataclass
class ConsoleEvent:
    ts: float
    level: str
    text: str
    url: str | None = None


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
    """CDP 主管单例对象，维护与浏览器的长连接及原生工具命令派发。"""

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
        if dialog_policy not in _VALID_POLICIES:
            raise ValueError(f"Invalid dialog_policy {dialog_policy!r}")
        self.task_id = task_id
        self.cdp_url = cdp_url
        self.launch_handle = launch_handle
        self.auto_owned = auto_owned
        self.dialog_policy = dialog_policy
        self.dialog_timeout_s = float(dialog_timeout_s)

        self._state_lock = threading.Lock()
        self._som_lock = threading.Lock()  # 序列化并发 annotated screenshot（注入→截图→清理）

        self._frames: dict[str, FrameInfo] = {}
        self._console_events: list[ConsoleEvent] = []
        self._active = False

        self._pending_downloads: dict[str, dict[str, Any]] = {}

        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False

        self._ws: Any = None
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future[dict[str, Any]]] = {}
        # 只保护替换；读走 _current_sids()。
        self._session_lock = threading.Lock()
        self._session_ids: SessionIds = SessionIds(active=None, page=None, root_frame="")
        self._attached_targets: dict[str, dict[str, str]] = {}

        # 弹窗 future 只在 loop 线程读写。
        self._dialog_open_waiters: set[asyncio.Future[None]] = set()
        # 导航 future 只在 loop 线程读写。
        self._frame_navigated_waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._lifecycle_waiters: dict[tuple[str, str], list[asyncio.Future[dict[str, Any]]]] = {}

        self._dialog_manager = DialogManager(
            policy=dialog_policy,
            timeout_s=dialog_timeout_s,
            cdp_send=self._cdp,
            loop_provider=lambda: self._loop,
        )
        self._refs = Refs(
            cdp_send_async=self._cdp,
            send_cdp_sync=self.send_cdp,
            evaluate_runtime=self.evaluate_runtime,
            loop_provider=lambda: self._loop,
            session_ids_provider=self._current_sids,
        )
        self._input = InputDispatch(
            send_cdp=self.send_cdp,
            evaluate_runtime=self.evaluate_runtime,
            resolve_ref=self._refs.resolve_ref_center,
            session_id_provider=lambda: (sids := self._current_sids()).active or sids.page,
            wait_for_page_stable=self.wait_for_page_stable,
        )

    def start(self, timeout: float = 15.0) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._ready_event.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name=f"cdp-supervisor-{self.task_id}",
                daemon=True,
            )
            self._thread.start()

        if not self._ready_event.wait(timeout=timeout):
            self.stop()
            raise TimeoutError(f"CDPSupervisor failed to connect to {self.cdp_url} within {timeout}s")
        if self._start_error is not None:
            err = self._start_error
            self.stop()
            raise err

    def stop(self) -> None:
        """取消 CDP 主任务并等待线程退出；自有浏览器随后终止。可重复调用。"""
        self._stop_requested = True
        loop, task = self._loop, self._main_task
        if loop is not None and task is not None:
            with contextlib.suppress(RuntimeError):  # loop 已关闭
                loop.call_soon_threadsafe(task.cancel)

        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("CDP supervisor %s thread did not exit within 5s", self.task_id)

        with self._state_lock:
            self._active = False

        if self.auto_owned and self.launch_handle is not None:
            self.launch_handle.terminate()

    @property
    def active(self) -> bool:
        """CDP 连接已建立且初始页已附着；断线重连期间为 False。"""
        return self._active

    def snapshot(self) -> SupervisorSnapshot:
        # DialogManager 锁不与 _state_lock 嵌套。
        pending, recent = self._dialog_manager.snapshot()
        with self._state_lock:
            active = self._active
            ft = self._build_frame_tree_locked()

        return SupervisorSnapshot(
            pending_dialogs=pending,
            recent_dialogs=recent,
            frame_tree=ft,
            active=active,
        )

    def _build_frame_tree_locked(self) -> dict[str, Any]:
        frames = list(self._frames.values())
        root = self._frames.get(self._current_sids().root_frame)
        if root is None:
            return {"frames_count": len(frames)}
        return {"root": root.to_dict(), "frames_count": len(frames)}

    def activate_tab_session(self, session_id: str) -> dict[str, Any]:
        """切到新 page session：启用事件域并跟 root_frame，否则导航等待器空等超时。"""
        for method, params in _PAGE_DOMAINS:
            result = self.send_cdp(method, params, session_id=session_id)
            if not result.get("ok"):
                return {"ok": False, "error": result.get("error", f"Failed to enable {method}")}
        ft = self.send_cdp("Page.getFrameTree", session_id=session_id)
        if not ft.get("ok"):
            return {"ok": False, "error": ft.get("error", "Failed to read the tab's frame tree")}
        frame = ft.get("result", {}).get("frameTree", {}).get("frame", {})
        root = frame.get("id", "")
        if not root:
            return {"ok": False, "error": "The tab has no root frame"}
        visible = self.send_cdp("Page.bringToFront", session_id=session_id)
        if not visible.get("ok"):
            return {"ok": False, "error": visible.get("error", "Failed to activate the browser page")}
        with self._state_lock:
            sids = self._current_sids()
            changed = (sids.active or sids.page) != session_id
            self._set_session(active=session_id, page=sids.page or session_id, root_frame=root)
            self._record_root_frame_locked(frame)
            for info in self._attached_targets.values():
                if info.get("session_id") == session_id:
                    info["frame_id"] = root
        if changed:
            self._refs.note_root_navigation()
        return {"ok": True}

    def _record_root_frame_locked(self, frame: dict[str, Any]) -> None:
        root = frame["id"]
        self._frames[root] = FrameInfo(
            frame_id=root,
            url=frame.get("url", ""),
            origin=frame.get("securityOrigin", ""),
            parent_frame_id=None,
            is_oopif=False,
            name=frame.get("name", ""),
        )

    def ensure_active_page(self) -> None:
        """关闭最后一页后附着现有页或创建空白页，不重启浏览器连接。"""
        if (sids := self._current_sids()).active or sids.page:
            return
        targets = self.list_tabs()
        if not targets.get("ok"):
            raise RuntimeError(targets.get("error", "Failed to list browser tabs"))
        page = next((t for t in targets.get("result", {}).get("targetInfos", []) if t.get("type") == "page"), None)
        if page is None:
            created = self.send_cdp("Target.createTarget", {"url": "about:blank"})
            if not created.get("ok"):
                raise RuntimeError(created.get("error", "Failed to create a browser tab"))
            target_id = created.get("result", {}).get("targetId")
        else:
            target_id = page.get("targetId")
        if not target_id:
            raise RuntimeError("The browser did not return a page target")
        attached = self.attach_target(target_id)
        if not attached.get("ok"):
            raise RuntimeError(attached.get("error", "Failed to attach to the browser tab"))
        session_id = attached.get("result", {}).get("sessionId")
        if not session_id:
            raise RuntimeError("The browser did not return a page session")
        activated = self.activate_tab_session(session_id)
        if not activated.get("ok"):
            raise RuntimeError(activated.get("error", "Failed to activate the browser tab"))

    def get_attached_targets(self) -> tuple[str | None, dict[str, dict[str, str]]]:
        with self._state_lock:
            return self._current_sids().active, dict(self._attached_targets)

    def _current_sids(self) -> SessionIds:
        """原子读取会话标识三元组。单属性读取在 CPython 下原子，无需持锁。"""
        return self._session_ids

    def _set_session(
        self,
        *,
        active: str | None | _Unset = _UNSET,
        page: str | None | _Unset = _UNSET,
        root_frame: str | _Unset = _UNSET,
    ) -> None:
        """原子写入会话标识三元组（仅修改传入字段，未传字段保持原值）。"""
        with self._session_lock:
            cur = self._session_ids
            self._session_ids = SessionIds(
                active=cur.active if isinstance(active, _Unset) else active,
                page=cur.page if isinstance(page, _Unset) else page,
                root_frame=cur.root_frame if isinstance(root_frame, _Unset) else root_frame,
            )

    def list_tabs(self) -> dict[str, Any]:
        return self.send_cdp("Target.getTargets")

    def attach_target(self, target_id: str) -> dict[str, Any]:
        result = self.send_cdp("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        if not result.get("ok"):
            return result
        session_id = result.get("result", {}).get("sessionId")
        if session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": ""}
        return result

    def close_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        with self._state_lock:
            if tab_id is None:
                for tid, info in self._attached_targets.items():
                    if info.get("session_id") == self._current_sids().active:
                        tab_id = tid
                        break
            closed_session = self._attached_targets.get(tab_id, {}).get("session_id") if tab_id is not None else None

        if tab_id is None:
            return {"ok": False, "error": "no tab to close (no active session)"}

        result = self.send_cdp("Target.closeTarget", {"targetId": tab_id})
        if not result.get("ok"):
            return result
        if result.get("result", {}).get("success") is False:
            return {"ok": False, "error": f"Browser did not close tab {tab_id}"}
        fallback = self._forget_target(tab_id, closed_session)
        if fallback is not None:
            # root_frame 须跟回退会话。
            ft = self.send_cdp("Page.getFrameTree", session_id=fallback)
            if ft.get("ok"):
                with self._state_lock:
                    if self._current_sids().active == fallback:
                        frame = ft["result"].get("frameTree", {}).get("frame", {})
                        if root := frame.get("id"):
                            self._set_session(root_frame=root)
                            self._record_root_frame_locked(frame)
        return {"ok": True, "tab_id": tab_id}

    def _forget_target(self, target_id: str | None, session_id: str | None) -> str | None:
        """移除关闭或断开的页会话，并把当前目标切到仍存活的已附着页。"""
        with self._state_lock:
            removed_ids = {
                info["session_id"]
                for tid, info in self._attached_targets.items()
                if tid == target_id or (session_id is not None and info.get("session_id") == session_id)
            }
            if session_id is not None:
                removed_ids.add(session_id)
            self._attached_targets = {
                tid: info for tid, info in self._attached_targets.items() if info.get("session_id") not in removed_ids
            }
            sids = self._current_sids()
            remaining = {info["session_id"]: info for info in self._attached_targets.values()}
            fallback = sids.active if sids.active in remaining else next(iter(remaining), None)
            active = fallback if sids.active in removed_ids else sids.active
            page = fallback if sids.page in removed_ids else sids.page
            selected = active or page
            changed = selected != (sids.active or sids.page)
            root = remaining.get(selected, {}).get("frame_id", "") if changed else sids.root_frame
            self._set_session(active=active, page=page, root_frame=root)
        if changed:
            self._refs.note_root_navigation()
        return selected if changed else None

    def send_cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            return {"ok": False, "error": "supervisor loop is not running"}

        sid = session_id or (sids := self._current_sids()).active or sids.page

        async def _do_send() -> dict[str, Any]:
            return await self._cdp(method, params, session_id=sid, timeout=timeout)

        try:
            fut = safe_schedule_threadsafe(_do_send(), loop)
            if fut is None:
                return {"ok": False, "error": "supervisor loop unavailable"}
            res = fut.result(timeout=timeout + 2)
            return {"ok": True, "result": res.get("result", res)}
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
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Supervisor loop is not running")

        async def _do_nav() -> dict[str, Any]:
            deadline = loop.time() + timeout
            sids = self._current_sids()
            sid = sids.active or sids.page
            target_frame_id = sids.root_frame

            navigated_fut = self._await_frame_navigated(target_frame_id)
            idle_fut: asyncio.Future[dict[str, Any]] | None = None
            try:
                nav = (await self._cdp("Page.navigate", {"url": url}, session_id=sid, timeout=timeout)).get(
                    "result",
                    {},
                )
                if nav.get("errorText"):
                    raise NavigationError(f"{nav['errorText']}: {url}")
                # 同文档 hash 跳转无 loaderId/事件。
                if loader_id := nav.get("loaderId"):
                    # 按 loaderId 等，防旧 networkIdle 放行。
                    idle_fut = self._await_lifecycle(loader_id, "networkIdle")
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
                title_resp = await self._cdp(
                    "Runtime.evaluate",
                    {"expression": "document.title", "returnByValue": True},
                    session_id=sid,
                    timeout=5.0,
                )
                title = title_resp.get("result", {}).get("result", {}).get("value", "")

            final_url = url
            with contextlib.suppress(Exception):
                url_resp = await self._cdp(
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
        loop = self._loop
        if loop is None or not loop.is_running():
            return False

        max_wait_ms = max(100, int(round(timeout_s * 1000)))
        debounce_ms = min(200, max(50, int(round(max_wait_ms / 3))))

        async def _do_wait() -> bool:
            sid = (sids := self._current_sids()).active or sids.page
            try:
                res = await self._cdp(
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
        sid = (sids := self._current_sids()).active or sids.page
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

    def console_messages(self, *, clear: bool = False) -> list[dict[str, Any]]:
        with self._state_lock:
            events = [{"ts": e.ts, "level": e.level, "text": e.text, "url": e.url} for e in self._console_events]
            if clear:
                self._console_events.clear()
        return events

    def screenshot(
        self,
        path: str | Path | None = None,
        *,
        full_page: bool = False,
        annotate: bool = False,
    ) -> dict[str, Any]:
        sid = (sids := self._current_sids()).active or sids.page
        elements: list[dict[str, Any]] = []
        annotation_context = ""

        with self._som_lock if annotate else contextlib.nullcontext():
            try:
                if annotate:
                    self.wait_for_page_stable(timeout_s=1.0)
                    inject_res = self.evaluate_runtime(SOM_INJECT_SCRIPT, timeout=5.0)
                    if not inject_res.get("ok"):
                        return {"ok": False, "error": f"SoM injection failed: {inject_res.get('error')}"}
                    raw_som = inject_res.get("result", "")
                    elements = parse_som_results(raw_som)
                    annotation_context = format_som_annotation_context(elements)
                    self._refs.record_som(elements)

                params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page}
                res = self.send_cdp("Page.captureScreenshot", params, session_id=sid, timeout=15.0)

                if not res.get("ok"):
                    return res

                data_b64 = res.get("result", {}).get("data", "")
                if not data_b64:
                    return {"ok": False, "error": "No screenshot data returned from CDP"}

                raw_bytes = base64.b64decode(data_b64)
                name_suffix = uuid.uuid4().hex[:8]
                out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"screenshot_{name_suffix}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(raw_bytes)

                result: dict[str, Any] = {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}
                if annotate:
                    result["elements"] = list(elements)
                    result["annotation_context"] = annotation_context
                return result
            finally:
                if annotate and not (cleanup := self.evaluate_runtime(SOM_REMOVE_SCRIPT, timeout=3.0)).get("ok"):
                    logger.warning("SoM cleanup failed; badges may remain on the page: %s", cleanup.get("error"))

    def execute_batch(
        self,
        actions: list[dict[str, Any]],
        wait_between_ms: int = 100,
        cancel_token: threading.Event | None = None,
    ) -> dict[str, Any]:
        """按序执行一组浏览器操作；任一步失败即停止，全部成功后等待页面沉降。"""
        if not actions or not isinstance(actions, list):
            return {"ok": False, "error": "actions must be a non-empty list"}

        try:
            if isinstance(wait_between_ms, bool):
                raise TypeError("bool not allowed")
            clamped_wait_ms = max(0, min(int(wait_between_ms), 5000))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": f"Invalid wait_between_ms '{wait_between_ms}' (must be a non-negative integer; milliseconds)",
            }
        results: list[dict[str, Any]] = []
        for i, act in enumerate(actions):
            if cancel_token is not None and cancel_token.is_set():
                return {
                    "ok": False,
                    "error": "Caller cancelled batch",
                    "cancelled": True,
                    "step": i,
                    "completed": results,
                }
            act_type = str(act.get("action", "")).strip().lower() if isinstance(act, dict) else ""
            res: dict[str, Any] = {"action": act_type, "step": i}
            try:
                res.update(self._run_batch_action(act, i, act_type, cancel_token))
            except Exception as exc:
                res.update({"ok": False, "error": f"Exception at step {i} ({act_type}): {exc}"})
            results.append(res)
            if not res.get("ok"):
                return {
                    "ok": False,
                    "error": res.get("error") or f"Action '{act_type}' failed",
                    "step": i,
                    "completed": results,
                }
            if dialog := res.get("dialog"):
                # 弹窗后整批勿重试。
                remaining = len(actions) - i - 1
                if remaining == 0:
                    return {"ok": True, "steps_executed": len(results), "details": results}
                return {
                    "ok": False,
                    "error": (
                        f"Step {i} ({act_type}) was performed and opened a JavaScript {dialog.get('type')} dialog, "
                        f"so the remaining {remaining} action(s) were not run. Respond with browser_dialog, "
                        "then run the remaining actions."
                    ),
                    "step": i,
                    "completed": results,
                }
            if clamped_wait_ms > 0 and i < len(actions) - 1:
                _pause(clamped_wait_ms / 1000.0, cancel_token)

        self.wait_for_page_stable(timeout_s=1.5)
        return {"ok": True, "steps_executed": len(results), "details": results}

    def _run_batch_action(
        self,
        act: object,
        step: int,
        act_type: str,
        cancel_token: threading.Event | None,
    ) -> dict[str, Any]:
        if not isinstance(act, dict):
            return {"ok": False, "error": f"Action at index {step} must be a dict"}
        raw_ref = act.get("ref")
        ref = "" if raw_ref is None else str(raw_ref).strip()

        if act_type == "click":
            return self.click_ref(ref, wait_stable=False)
        if act_type == "type":
            return self.type_ref(ref, str(act.get("text", "")), wait_stable=False)
        if act_type == "press":
            key = act.get("key")
            if not isinstance(key, str) or not key.strip():
                return {"ok": False, "error": f"'press' action at step {step} requires a non-empty 'key' field"}
            return self.press_key(key.strip())
        if act_type == "hover":
            return self.hover_ref(ref)
        if act_type == "scroll":
            try:
                pixels = int(round(parse_numeric_unit(act.get("pixels"), 500, valid_units=("px",))))
            except ValueError:
                return {"ok": False, "error": f"Invalid scroll pixels '{act.get('pixels')}' at step {step}"}
            return self.scroll_page(str(act.get("direction", "down")), pixels)
        if act_type == "wait":
            try:
                wait_s = parse_numeric_unit(act.get("seconds"), 1.0, valid_units=("s", "ms"))
            except ValueError:
                return {"ok": False, "error": f"Invalid wait duration '{act.get('seconds')}' at step {step}"}
            clamped_s = max(0.0, min(wait_s, 10.0))
            _pause(clamped_s, cancel_token)
            return {"ok": True, "waited": clamped_s}
        if act_type == "select":
            return self._run_batch_select(act, step, ref)
        return {"ok": False, "error": f"Unknown action type '{act_type}' at step {step}"}

    def _run_batch_select(self, act: dict[str, Any], step: int, ref: str) -> dict[str, Any]:
        raw_val = act.get("value")
        val = str(raw_val) if raw_val is not None and not isinstance(raw_val, bool) else None
        raw_label = act.get("label")
        label = raw_label if isinstance(raw_label, str) and raw_label.strip() else None
        raw_idx = act.get("index")
        try:
            idx = int(raw_idx) if raw_idx is not None else None
        except (TypeError, ValueError):
            return {"ok": False, "error": f"Invalid select index '{raw_idx}' at step {step}"}
        if val is None and label is None and idx is None:
            return {"ok": False, "error": f"'select' action at step {step} requires one of 'value', 'label', 'index'"}
        sel_res = self.select_ref(ref, value=val, label=label, index=idx)
        if not sel_res.get("success"):
            return {"ok": False, "error": sel_res.get("error", "Select failed")}
        return {"ok": True, "selected": sel_res.get("selected") or sel_res.get("text") or ref}

    def screenshot_element(self, ref: str, path: str | Path | None = None) -> dict[str, Any]:
        try:
            # 先滚入视口再截。
            _, _, obj_id = self._refs.resolve_ref_center(ref, scroll_into_view=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not obj_id:
            return {
                "ok": False,
                "error": f"Element '{ref}' could not be resolved to a DOM element (coordinate-based refs or elements no longer in the DOM cannot be used for screenshot_element)",
            }

        sid = (sids := self._current_sids()).active or sids.page
        box = self.send_cdp("DOM.getBoxModel", {"objectId": obj_id}, session_id=sid)
        if not box.get("ok"):
            return {"ok": False, "error": f"Failed to get box model for {ref}: {box.get('error')}"}

        quad = box["result"].get("model", {}).get("border", [])
        if len(quad) < 8:
            return {"ok": False, "error": f"Invalid box model dimensions for {ref}"}
        # clip 是文档坐标，须加滚动偏移。
        metrics = self.send_cdp("Page.getLayoutMetrics", session_id=sid)
        if not metrics.get("ok"):
            return {"ok": False, "error": f"Failed to read page scroll offset: {metrics.get('error')}"}
        viewport = metrics["result"].get("cssVisualViewport", {})

        xs, ys = quad[0::2], quad[1::2]
        if (
            max(xs) <= 0
            or max(ys) <= 0
            or min(xs) >= viewport.get("clientWidth", 0)
            or min(ys) >= viewport.get("clientHeight", 0)
        ):
            return {
                "ok": False,
                "error": f"Element '{ref}' is not visible in the viewport (it may be hidden or clipped)",
            }
        clip = {
            "x": min(xs) + viewport.get("pageX", 0),
            "y": min(ys) + viewport.get("pageY", 0),
            "width": max(1, max(xs) - min(xs)),
            "height": max(1, max(ys) - min(ys)),
            "scale": 1,
        }
        res = self.send_cdp(
            "Page.captureScreenshot",
            {"format": "png", "clip": clip},
            session_id=sid,
            timeout=15.0,
        )
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path) if path else Path(tempfile.gettempdir()) / f"element_{uuid.uuid4().hex[:8]}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def print_pdf(
        self,
        path: str | Path,
        *,
        landscape: bool = False,
        print_background: bool = True,
        paper_width: float = 8.5,
        paper_height: float = 11.0,
    ) -> dict[str, Any]:
        sid = (sids := self._current_sids()).active or sids.page
        params = {
            "landscape": landscape,
            "printBackground": print_background,
            "paperWidth": paper_width,
            "paperHeight": paper_height,
        }
        res = self.send_cdp("Page.printToPDF", params, session_id=sid, timeout=20.0)
        if not res.get("ok"):
            return res

        data_b64 = res.get("result", {}).get("data", "")
        raw_bytes = base64.b64decode(data_b64)
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        return {"ok": True, "path": str(out_path), "bytes": len(raw_bytes)}

    def wait_for_download(
        self,
        timeout: float = 30.0,
        cancel_token: threading.Event | None = None,
        *,
        started_after: float = 0.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        guid: str | None = None
        while time.monotonic() < deadline:
            if self._stop_requested or (cancel_token is not None and cancel_token.is_set()):
                return {"ok": False, "error": "Download wait was cancelled; the download may still complete"}
            with self._state_lock:
                if guid is None:
                    guid = next(
                        (key for key, value in self._pending_downloads.items() if value["started_at"] >= started_after),
                        None,
                    )
                entry = self._pending_downloads.get(guid) if guid is not None else None
                if entry is not None and entry["event"].is_set():
                    self._pending_downloads.pop(guid, None)
                    state = entry.get("state", "unknown")
                    if state == "completed":
                        return {
                            "ok": True,
                            "filename": entry.get("filename", ""),
                            "path": entry.get("file_path", ""),
                            "guid": guid,
                        }
                    return {"ok": False, "error": f"download ended with state: {state}"}
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return {"ok": False, "error": f"download timed out after {timeout}s; it may still complete"}

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._main_task = loop.create_task(self._run(), name=f"cdp-supervisor-{self.task_id}")
        self._loop = loop
        try:
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass  # stop() 取消主任务
        except Exception as e:
            if not self._ready_event.is_set():
                self._start_error = e
            else:
                logger.warning("CDP supervisor %s crashed: %s", self.task_id, e)
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._state_lock:
                self._active = False
            if not self._ready_event.is_set():
                self._start_error = self._start_error or RuntimeError("CDP supervisor stopped before connecting")
                self._ready_event.set()

    async def _run(self) -> None:
        backoff = 0.5
        while not self._stop_requested:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(self.cdp_url, max_size=50 * 1024 * 1024, close_timeout=2.0),
                    timeout=10.0,
                )
            except Exception as e:
                if not self._ready_event.is_set():
                    raise
                logger.warning("CDP supervisor %s reconnect failed: %s", self.task_id, e)
                if self._launched_browser_exited():
                    return
                await asyncio.sleep(random.uniform(0.0, backoff))
                backoff = min(backoff * 2, _CDP_BACKOFF_MAX)
                continue

            self._ws = ws
            reader_task = asyncio.create_task(self._read_loop(ws), name="cdp-reader")
            try:
                with self._state_lock:
                    self._set_session(page=None, active=None)
                    self._attached_targets.clear()

                await self._attach_initial_page()
                with self._state_lock:
                    self._active = True
                backoff = 0.5
                self._ready_event.set()

                await reader_task
            except Exception as e:
                if not self._ready_event.is_set():
                    raise
                logger.warning("CDP supervisor %s session dropped: %s", self.task_id, e)
            finally:
                with self._state_lock:
                    self._active = False
                if not reader_task.done():
                    reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader_task
                for fut in list(self._pending_calls.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("CDP connection lost"))
                self._pending_calls.clear()
                self._ws = None
                with contextlib.suppress(Exception):
                    await ws.close()

            if self._launched_browser_exited():
                return
            await asyncio.sleep(random.uniform(0.0, backoff))
            backoff = min(backoff * 2, _CDP_BACKOFF_MAX)

    def _launched_browser_exited(self) -> bool:
        """自行启动的浏览器进程已退出（崩溃或用户关闭窗口）时不再重连，由下次导航重新启动。"""
        if not self.auto_owned or self.launch_handle is None or (code := self.launch_handle.poll()) is None:
            return False
        logger.warning("CDP supervisor %s: browser process exited with code %s; not reconnecting", self.task_id, code)
        return True

    async def _enable_page_domains(self, session_id: str) -> None:
        for method, params in _PAGE_DOMAINS:
            await self._cdp(method, params, session_id=session_id)

    async def _attach_initial_page(self) -> None:
        resp = await self._cdp("Target.getTargets")
        targets = resp.get("result", {}).get("targetInfos", [])
        page_target = next((t for t in targets if t.get("type") == "page"), None)
        if page_target is None:
            created = await self._cdp("Target.createTarget", {"url": "about:blank"})
            target_id = created["result"]["targetId"]
        else:
            target_id = page_target["targetId"]

        attach = await self._cdp("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        page_sid = attach["result"]["sessionId"]
        with self._state_lock:
            self._set_session(page=page_sid, active=page_sid)
            self._attached_targets[target_id] = {"session_id": page_sid, "title": ""}

        sid = page_sid

        ft_resp = await self._cdp("Page.getFrameTree", session_id=sid)
        frame = ft_resp.get("result", {}).get("frameTree", {}).get("frame", {})
        root = frame.get("id", "")
        with self._state_lock:
            self._set_session(root_frame=root)
            if root:
                self._record_root_frame_locked(frame)
            self._attached_targets[target_id]["frame_id"] = root

        await self._enable_page_domains(sid)
        await self._cdp("Page.bringToFront", session_id=sid)
        await self._cdp(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "eventsEnabled": True, "downloadPath": tempfile.gettempdir()},
        )
        await self._cdp(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
            session_id=sid,
        )

    async def _cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Supervisor WebSocket is not connected")
        call_id = self._next_call_id
        self._next_call_id += 1
        payload: dict[str, Any] = {"id": call_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id

        pre_existing = None
        if method != "Page.handleJavaScriptDialog":
            pre_existing = self._dialog_manager.pending_for(session_id)
            # 弹窗未决时输入会被丢弃。
            if pre_existing is not None and method.startswith("Input."):
                raise DialogBlockedError(pre_existing, opened_by_call=False)

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = fut
        try:
            await self._ws.send(json.dumps(payload))
            if method == "Page.handleJavaScriptDialog":
                return await asyncio.wait_for(fut, timeout=timeout)
            tolerated = pre_existing.id if pre_existing is not None and method in _NAVIGATION_METHODS else None
            return await self._await_response(
                fut,
                session_id,
                timeout,
                pre_existing_id=pre_existing.id if pre_existing is not None else None,
                tolerated_id=tolerated,
            )
        finally:
            self._pending_calls.pop(call_id, None)

    async def _await_response(
        self,
        fut: asyncio.Future[dict[str, Any]],
        session_id: str | None,
        timeout: float,
        *,
        pre_existing_id: str | None,
        tolerated_id: str | None,
    ) -> dict[str, Any]:
        """等 CDP 响应；弹窗阻塞抛 DialogBlockedError。tolerated_id 为不判阻塞的已有弹窗。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if (dialog := self._dialog_manager.pending_for(session_id, exclude_id=tolerated_id)) is not None:
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), min(_DIALOG_BLOCK_GRACE_S, max(0.0, remaining)))
                except TimeoutError:
                    raise DialogBlockedError(dialog, opened_by_call=dialog.id != pre_existing_id) from None
            opened: asyncio.Future[None] = loop.create_future()
            self._dialog_open_waiters.add(opened)
            try:
                done, _ = await asyncio.wait(
                    {fut, opened},
                    timeout=max(0.0, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                self._dialog_open_waiters.discard(opened)
                opened.cancel()
            if fut in done:
                return fut.result()
            if not done:
                raise TimeoutError(f"CDP response not received within {timeout}s")

    async def _read_loop(self, ws: Any) -> None:
        """分派 CDP 响应与事件；连接异常关闭时抛出，由 _run 记录并重连。单个事件处理失败只记录，不断开连接。"""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                logger.debug("CDP supervisor %s dropped non-JSON frame", self.task_id)
                continue

            if "id" in msg:
                fut = self._pending_calls.pop(msg["id"], None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(f"CDP error on id={msg['id']}: {msg['error']}"))
                    else:
                        fut.set_result(msg)
            elif "method" in msg:
                try:
                    self._on_event(msg["method"], msg.get("params", {}), msg.get("sessionId"))
                except Exception:
                    logger.exception("CDP supervisor %s failed to handle %s", self.task_id, msg["method"])

    def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Page.frameNavigated":
            self._on_frame_navigated(params, session_id)
            self._dispatch_frame_navigated(params)
        elif method == "Page.lifecycleEvent":
            self._dispatch_lifecycle(params)
        elif method == "Page.javascriptDialogOpening":
            self._on_dialog_opening(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            self._dialog_manager.on_remote_closed(session_id)
        elif method == "Page.frameAttached":
            self._on_frame_attached(params, session_id)
        elif method == "Page.frameDetached":
            self._on_frame_detached(params)
        elif method == "Target.attachedToTarget":
            self._on_target_attached(params)
        elif method == "Target.detachedFromTarget":
            self._on_target_detached(params)
        elif method == "Runtime.consoleAPICalled":
            self._on_console(params, level_from="api")
        elif method == "Runtime.exceptionThrown":
            self._on_console(params, level_from="exception")
        elif method == "Browser.downloadWillBegin":
            self._on_download_begin(params)
        elif method == "Browser.downloadProgress":
            self._on_download_progress(params)

    def _await_frame_navigated(self, frame_id: str) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        bucket = self._frame_navigated_waiters.setdefault(frame_id, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, fid=frame_id: self._pop_waiter(self._frame_navigated_waiters, fid, f))
        return fut

    def _await_lifecycle(self, loader_id: str, name: str) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        key = (loader_id, name)
        bucket = self._lifecycle_waiters.setdefault(key, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, k=key: self._pop_waiter(self._lifecycle_waiters, k, f))
        return fut

    def _pop_waiter(
        self,
        store: dict[Any, list[asyncio.Future[dict[str, Any]]]],
        key: object,
        fut: asyncio.Future[dict[str, Any]],
    ) -> None:
        bucket = store.get(key)
        if bucket is None:
            return
        if fut in bucket:
            bucket.remove(fut)
        if not bucket:
            store.pop(key, None)

    def _dispatch_frame_navigated(self, params: dict[str, Any]) -> None:
        fid = params.get("frame", {}).get("id", "")
        waiters = self._frame_navigated_waiters.pop(fid, ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)

    def _dispatch_lifecycle(self, params: dict[str, Any]) -> None:
        waiters = self._lifecycle_waiters.pop((params.get("loaderId", ""), params.get("name", "")), ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)

    def _on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame", {})
        fid = frame.get("id", "")
        if not fid:
            return
        parent_id = frame.get("parentId")
        with self._state_lock:
            sids = self._current_sids()
            selected = sids.active or sids.page
            if not parent_id and session_id == selected:
                self._set_session(root_frame=fid)
                sids = self._current_sids()
            if not parent_id:
                for info in self._attached_targets.values():
                    if info.get("session_id") == session_id:
                        info["frame_id"] = fid
            page_sessions = {info.get("session_id") for info in self._attached_targets.values()}
            self._frames[fid] = FrameInfo(
                frame_id=fid,
                url=frame.get("url", "") or "",
                origin=frame.get("securityOrigin", ""),
                parent_frame_id=parent_id,
                is_oopif=session_id is not None and session_id != sids.page and session_id not in page_sessions,
                name=frame.get("name", ""),
            )
        if not parent_id and session_id == selected:
            self._refs.note_root_navigation()

    def _on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        pid = params.get("parentFrameId")
        if fid:
            with self._state_lock:
                self._frames[fid] = FrameInfo(frame_id=fid, url="", origin="", parent_frame_id=pid, is_oopif=False)

    def _on_frame_detached(self, params: dict[str, Any]) -> None:
        fid = params.get("frameId", "")
        if fid:
            with self._state_lock:
                self._frames.pop(fid, None)

    def _on_target_attached(self, params: dict[str, Any]) -> None:
        target_info = params.get("targetInfo", {})
        session_id = params.get("sessionId", "")
        target_id = target_info.get("targetId", "")
        if target_info.get("type") == "page" and target_id and session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": target_info.get("title", "")}

    def _on_target_detached(self, params: dict[str, Any]) -> None:
        self._forget_target(params.get("targetId"), params.get("sessionId"))

    def _on_console(self, params: dict[str, Any], level_from: str) -> None:
        ts = time.time()
        if level_from == "exception":
            details = params.get("exceptionDetails", {})
            text = details.get("text") or "Uncaught exception"
            url = details.get("url")
            event = ConsoleEvent(ts=ts, level="exception", text=text, url=url)
        else:
            level = params.get("type", "log")
            args = params.get("args", [])
            text_parts = []
            for a in args:
                val = a.get("value")
                if val is not None:
                    text_parts.append(str(val))
                else:
                    text_parts.append(a.get("description", ""))
            event = ConsoleEvent(ts=ts, level=level, text=" ".join(text_parts))

        with self._state_lock:
            self._console_events.append(event)
            if len(self._console_events) > CONSOLE_HISTORY_MAX:
                self._console_events.pop(0)

    def _on_download_begin(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        if not guid:
            return
        with self._state_lock:
            self._pending_downloads[guid] = {
                "state": "in_progress",
                "started_at": time.monotonic(),
                "filename": params.get("suggestedFilename", ""),
                "event": threading.Event(),
            }

    def _on_download_progress(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        state = params.get("state", "")
        with self._state_lock:
            entry = self._pending_downloads.get(guid)
            if entry is not None:
                entry["state"] = state
                if state == "completed":
                    entry["file_path"] = params.get("filePath", "")
                if state in ("completed", "canceled"):
                    entry["event"].set()

    def _on_dialog_opening(self, params: dict[str, Any], session_id: str | None) -> None:
        dialog = PendingDialog(
            id=self._dialog_manager.next_id(),
            type=str(params.get("type") or ""),
            message=str(params.get("message") or ""),
            default_prompt=str(params.get("defaultPrompt") or ""),
            opened_at=time.time(),
            cdp_session_id=session_id or self._current_sids().page or "",
            frame_id=params.get("frameId"),
        )
        self._dialog_manager.open(dialog)
        for waiter in self._dialog_open_waiters:
            if not waiter.done():
                waiter.set_result(None)

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
            active_session_id=(sids := self._current_sids()).active or sids.page,
        )


def _pause(seconds: float, cancel_token: threading.Event | None) -> None:
    """可被取消令牌提前唤醒的等待。"""
    if cancel_token is not None:
        cancel_token.wait(seconds)
    else:
        time.sleep(seconds)


@dataclass
class _LifecycleState:
    lock: RLock = field(default_factory=RLock)
    users: int = 0


class SupervisorRegistry:
    def __init__(self) -> None:
        self._supervisors: dict[str, CDPSupervisor] = {}
        self._lifecycles: dict[str, _LifecycleState] = {}
        self._lock = threading.Lock()

    @contextmanager
    def lifecycle(self, task_id: str) -> Iterator[None]:
        """同 task 的启动、替换和停止共用锁；等待者也持有锁条目，避免回收后分裂成两把锁。"""
        with self._lock:
            if (state := self._lifecycles.get(task_id)) is None:
                state = _LifecycleState()
                self._lifecycles[task_id] = state
            state.users += 1
        try:
            with state.lock:
                yield
        finally:
            with self._lock:
                state.users -= 1
                if state.users == 0 and task_id not in self._supervisors:
                    self._lifecycles.pop(task_id, None)

    def get(self, task_id: str) -> CDPSupervisor | None:
        with self._lock:
            return self._supervisors.get(task_id)

    def get_or_start(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: NativeBrowserProcess | None = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        timeout: float = 15.0,
    ) -> CDPSupervisor:
        with self.lifecycle(task_id):
            existing = self.get(task_id)
            if existing is not None and existing.active:
                return existing
            self.stop(task_id)
            sup = CDPSupervisor(
                task_id=task_id,
                cdp_url=cdp_url,
                launch_handle=launch_handle,
                auto_owned=auto_owned,
                dialog_policy=dialog_policy,
                dialog_timeout_s=dialog_timeout_s,
            )
            try:
                sup.start(timeout=timeout)
            except BaseException:
                try:
                    sup.stop()
                except Exception:
                    logger.exception("Error stopping failed supervisor %s", task_id)
                raise
            with self._lock:
                self._supervisors[task_id] = sup
            return sup

    def stop(self, task_id: str, *, expected: CDPSupervisor | None = None) -> None:
        with self.lifecycle(task_id):
            with self._lock:
                sup = self._supervisors.get(task_id)
                if expected is not None and sup is not expected:
                    return
                self._supervisors.pop(task_id, None)
            if sup is not None:
                sup.stop()


SUPERVISOR_REGISTRY = SupervisorRegistry()
