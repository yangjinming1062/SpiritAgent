import asyncio
import base64
import contextlib
import json
import logging
import random
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from utils import safe_schedule_threadsafe

from .dialog_manager import (
    _VALID_POLICIES,
    DEFAULT_DIALOG_POLICY,
    DEFAULT_DIALOG_TIMEOUT_S,
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
from .input import InputDispatch, parse_numeric_unit
from .refs import Refs, SessionIds

logger = logging.getLogger(__name__)


_CDP_BACKOFF_MAX = 10.0

_UNSET: Any = object()

CONSOLE_HISTORY_MAX = 50

DIALOG_BRIDGE_HOST = "spiritagent-dialog-bridge.invalid"
DIALOG_BRIDGE_URL_PATTERN = f"http://{DIALOG_BRIDGE_HOST}/*"


_DIALOG_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__spiritagentDialogBridgeInstalled) return;
  window.__spiritagentDialogBridgeInstalled = true;
  const ENDPOINT = "http://spiritagent-dialog-bridge.invalid/";
  function ask(kind, message, defaultPrompt) {
    try {
      const xhr = new XMLHttpRequest();
      const params = new URLSearchParams({
        kind: String(kind || ""),
        message: String(message == null ? "" : message),
        default_prompt: String(defaultPrompt == null ? "" : defaultPrompt),
      });
      xhr.open("GET", ENDPOINT + "?" + params.toString(), false);
      xhr.send(null);
      if (xhr.status !== 200) return null;
      const body = xhr.responseText || "";
      let parsed;
      try { parsed = JSON.parse(body); } catch (e) { return null; }
      if (kind === "alert") return undefined;
      if (kind === "confirm") return Boolean(parsed && parsed.accept);
      if (kind === "prompt") {
        if (!parsed || !parsed.accept) return null;
        return parsed.prompt_text == null ? "" : String(parsed.prompt_text);
      }
      return null;
    } catch (e) {
      return null;
    }
  }
  window.alert   = function(message) { ask("alert",   message, ""); };
  window.confirm = function(message) {
    const r = ask("confirm", message, "");
    return r === null ? false : Boolean(r);
  };
  window.prompt  = function(message, def) {
    const r = ask("prompt", message, def == null ? "" : def);
    return r === null ? null : String(r);
  };
})();
"""


class NavigationError(Exception):
    """CDP 导航返回错误。"""


@dataclass
class FrameInfo:
    frame_id: str
    url: str
    origin: str
    parent_frame_id: str | None
    is_oopif: bool
    cdp_session_id: str | None = None
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
    console_errors: tuple[ConsoleEvent, ...]
    active: bool
    cdp_url: str
    task_id: str

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
        launch_handle: Any = None,
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
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False

        self._ws: Any = None
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future] = {}
        # 锁 #5：仅保护 _session_ids tuple 的原子替换。读路径走 _current_sids() 无锁。
        self._session_lock = threading.Lock()
        self._session_ids: SessionIds = SessionIds(active=None, page=None, root_frame="")
        self._attached_targets: dict[str, dict[str, str]] = {}
        self._child_sessions: dict[str, str] = {}

        # 事件回调钩子（供 navigate 等一次性等待使用）
        # 一次性等待 future：dict 按 frame_id / (frame_id, name) 索引；
        # 单 loop 线程访问（_await_* 在 loop 线程跑，_on_event 在 loop 线程触发）。
        self._frame_navigated_waiters: dict[str, list[asyncio.Future]] = {}
        self._lifecycle_waiters: dict[tuple[str, str], list[asyncio.Future]] = {}

        # DialogManager 和 Refs 在 _cdp 绑定为 bound method 之后才能取，
        # 故用 lambda 延迟到首次调用时取。
        self._dialog_manager = DialogManager(
            policy=dialog_policy,
            timeout_s=dialog_timeout_s,
            cdp_send=self._cdp,
            loop_provider=lambda: self._loop,
            session_id_provider=lambda: (sids := self._current_sids()).active or sids.page,
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
            resolve_ref=self._refs._resolve_ref_center,
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
        self._stop_requested = True
        loop = self._loop
        if self._dialog_manager is not None:
            # 先取消所有看门狗、对未决 bridge dialog 发 dismiss，避免页面端 XHR 卡死。
            self._dialog_manager.shutdown()
            # 再 cancel-and-await 所有 DialogManager 后台任务（gather+return_exceptions），
            # 然后才关闭 loop，避免协程在 Event loop is closed 状态下退出。
            if loop is not None and loop.is_running():
                self._dialog_manager.bg.drain(loop)
        if loop is not None and loop.is_running():
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        with self._state_lock:
            self._active = False

        if self.auto_owned and self.launch_handle is not None:
            try:
                self.launch_handle.terminate()
            except Exception as e:
                logger.debug("Error terminating launch_handle: %s", e)

    def snapshot(self) -> SupervisorSnapshot:
        # DialogManager.snapshot 单独加锁（锁 #2），不与 _state_lock 嵌套以避免锁序倒置。
        pending, recent = self._dialog_manager.snapshot()
        with self._state_lock:
            console_errors = tuple(e for e in self._console_events if e.level in ("error", "exception"))
            active = self._active
            ft = self._build_frame_tree_locked()

        return SupervisorSnapshot(
            pending_dialogs=pending,
            recent_dialogs=recent,
            frame_tree=ft,
            console_errors=console_errors,
            active=active,
            cdp_url=self.cdp_url,
            task_id=self.task_id,
        )

    def _build_frame_tree_locked(self) -> dict[str, Any]:
        frames = list(self._frames.values())
        root = next((f for f in frames if not f.parent_frame_id), None)
        if root is None and frames:
            root = frames[0]
        if root is None:
            return {"frames_count": 0}
        return {"root": root.to_dict(), "frames_count": len(frames)}

    def get_frame_session(self, frame_id: str) -> tuple[FrameInfo | None, str | None]:
        with self._state_lock:
            frame = self._frames.get(frame_id)
            if frame is None:
                return None, None
            return frame, frame.cdp_session_id

    def set_active_session_id(self, session_id: str | None) -> None:
        self._set_session(active=session_id)

    def get_attached_targets(self) -> tuple[str | None, dict[str, dict[str, str]]]:
        with self._state_lock:
            return self._current_sids().active, dict(self._attached_targets)

    def _current_sids(self) -> SessionIds:
        """原子读取会话标识三元组。单属性读取在 CPython 下原子，无需持锁。"""
        return self._session_ids

    def _set_session(
        self,
        *,
        active: str | None | object = _UNSET,
        page: str | None | object = _UNSET,
        root_frame: str | object = _UNSET,
    ) -> None:
        """原子写入会话标识三元组（仅修改传入字段，未传字段保持原值）。"""
        with self._session_lock:
            cur = self._session_ids
            self._session_ids = SessionIds(
                active=cur.active if active is _UNSET else active,  # type: ignore[arg-type]
                page=cur.page if page is _UNSET else page,  # type: ignore[arg-type]
                root_frame=cur.root_frame if root_frame is _UNSET else root_frame,  # type: ignore[arg-type]
            )

    def list_tabs(self) -> dict[str, Any]:
        return self.send_cdp("Target.getTargets")

    def _attach_target(self, target_id: str) -> dict[str, Any]:
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
            closing_active = (
                tab_id is not None
                and self._attached_targets.get(tab_id, {}).get("session_id") == self._current_sids().active
            )

        if tab_id is None:
            return {"ok": False, "error": "no tab to close (no active session)"}

        result = self.send_cdp("Target.closeTarget", {"targetId": tab_id})
        with self._state_lock:
            self._attached_targets.pop(tab_id, None)
            if closing_active:
                self._set_session(active=self._current_sids().page)
        return result if not result.get("ok") else {"ok": True, "tab_id": tab_id}

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

    def navigate(self, url: str, *, wait_until: str = "networkIdle", timeout: float = 30.0) -> dict[str, Any]:
        """导航到 URL 并等待页面帧就绪。"""
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("Supervisor loop is not running")

        async def _do_nav() -> dict[str, Any]:
            sid = (sids := self._current_sids()).active or sids.page
            target_frame_id = sids.root_frame

            navigated_fut = self._await_frame_navigated(target_frame_id)
            idle_fut = self._await_lifecycle(target_frame_id, "networkIdle")

            nav_resp = await self._cdp("Page.navigate", {"url": url}, session_id=sid, timeout=timeout)
            if "result" in nav_resp and nav_resp["result"].get("errorText"):
                err_text = nav_resp["result"]["errorText"]
                raise NavigationError(f"{err_text}: {url}")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(navigated_fut, timeout=min(timeout, 20.0))

            if wait_until == "networkIdle":
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(idle_fut, timeout=min(timeout, 5.0))

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
        return fut.result(timeout=timeout + 5)

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
        full: bool = False,
        interactive_only: bool = False,
        max_depth: int = 50,
    ) -> dict[str, Any]:
        """抓取 AXTree 并生成 [ref=eN] 文本快照，同步在 DOM 中注入 aria-ref 属性。"""
        return self._refs.snapshot_axtree(full=full, interactive_only=interactive_only, max_depth=max_depth)

    async def _inject_aria_refs_async(self, refs_map: dict[str, dict[str, Any]], session_id: str | None) -> None:
        await self._refs._inject_aria_refs_async(refs_map, session_id)

    def _resolve_ref_center(self, ref: str, *, scroll_into_view: bool = True) -> tuple[float, float, str | None]:
        """根据 ref、视觉角标序号或视口物理坐标定位并返回 (center_x, center_y, object_id)。"""
        return self._refs._resolve_ref_center(ref, scroll_into_view=scroll_into_view)

    def click_ref(self, ref: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        return self._input.click_ref(ref, wait_stable=wait_stable, timeout_s=timeout_s)

    def type_ref(self, ref: str, text: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        """先聚焦并清空元素，再输入新文本。"""
        return self._input.type_ref(ref, text, wait_stable=wait_stable, timeout_s=timeout_s)

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
    ) -> dict[str, Any]:
        return self._input.wait_for(selector=selector, text=text, timeout_s=timeout_s)

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
        elements = []
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
                    # 每个 ref 键（ref_raw / ref）写入独立 dict，避免下游对单一键的 mutation 误改其余两份。
                    # 不写 str(index)：会与 AXTree 的 eN 命名空间冲突。
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
                if annotate:
                    try:
                        self.evaluate_runtime(SOM_REMOVE_SCRIPT, timeout=3.0)
                    except Exception as cleanup_exc:
                        logger.debug("SoM cleanup exception: %s", cleanup_exc)

    def execute_batch(
        self,
        actions: list[dict[str, Any]],
        wait_between_ms: int = 100,
        cancel_token: Any = None,
    ) -> dict[str, Any]:
        """按序连续执行一组浏览器操作，并在操作间执行沉降等待与错误拦截。"""
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
        results = []
        failed: dict[str, Any] | None = None
        for i, act in enumerate(actions):
            # 取消令牌触发时立刻跳出整个 batch, 不再执行后续动作, 不浪费 IPC 帧。
            if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
                return {
                    "ok": False,
                    "error": "Caller cancelled batch",
                    "cancelled": True,
                    "step": i,
                    "completed": results,
                }
            if not isinstance(act, dict):
                failed = {"ok": False, "error": f"Action at index {i} must be a dict", "step": i, "completed": results}
                break

            raw_act = act.get("action") if "action" in act else act.get("type", "")
            act_type = str(raw_act).strip().lower()

            raw_ref = act.get("ref") if "ref" in act else act.get("selector", "")
            ref = str(raw_ref).strip() if raw_ref is not None else ""
            res: dict[str, Any] = {"action": act_type, "step": i}

            try:
                if act_type == "click":
                    res.update(self.click_ref(ref, wait_stable=False))
                elif act_type == "type":
                    text = str(act.get("text", ""))
                    res.update(self.type_ref(ref, text, wait_stable=False))
                elif act_type == "press":
                    key = act.get("key")
                    if not isinstance(key, str) or not key.strip():
                        res.update(
                            {"ok": False, "error": f"'press' action at step {i} requires a non-empty 'key' field"},
                        )
                        results.append(res)
                        failed = {
                            "ok": False,
                            "error": f"'press' action at step {i} requires a non-empty 'key' field",
                            "step": i,
                            "completed": results,
                        }
                        break
                    res.update(self.press_key(key.strip()))
                elif act_type == "hover":
                    res.update(self.hover_ref(ref))
                elif act_type == "scroll":
                    direction = str(act.get("direction", "down"))
                    try:
                        pixels = int(round(parse_numeric_unit(act.get("pixels"), 500, valid_units=("px",))))
                    except ValueError:
                        res.update({"ok": False, "error": f"Invalid scroll pixels '{act.get('pixels')}' at step {i}"})
                        results.append(res)
                        failed = {
                            "ok": False,
                            "error": f"Invalid scroll pixels '{act.get('pixels')}' at step {i}",
                            "step": i,
                            "completed": results,
                        }
                        break
                    res.update(self.scroll_page(direction, pixels))
                elif act_type == "wait":
                    raw_wait = act.get("seconds", act.get("time"))
                    try:
                        wait_s = parse_numeric_unit(raw_wait, 1.0, valid_units=("s", "ms"))
                    except ValueError:
                        res.update({"ok": False, "error": f"Invalid wait duration '{raw_wait}' at step {i}"})
                        results.append(res)
                        failed = {
                            "ok": False,
                            "error": f"Invalid wait duration '{raw_wait}' at step {i}",
                            "step": i,
                            "completed": results,
                        }
                        break
                    clamped_s = max(0.0, min(wait_s, 10.0))
                    if clamped_s > 0:
                        time.sleep(clamped_s)
                    res.update({"ok": True, "waited": clamped_s})
                elif act_type == "select":
                    raw_val = act.get("value")
                    val = str(raw_val) if raw_val is not None and not isinstance(raw_val, bool) else None
                    raw_label = act.get("label")
                    label = raw_label if isinstance(raw_label, str) and raw_label.strip() else None
                    raw_idx = act.get("index")
                    try:
                        idx = int(raw_idx) if raw_idx is not None else None
                    except (TypeError, ValueError):
                        results.append({**res, "ok": False, "error": f"Invalid select index '{raw_idx}' at step {i}"})
                        failed = {
                            "ok": False,
                            "error": f"Invalid select index '{raw_idx}' at step {i}",
                            "step": i,
                            "completed": results,
                        }
                        break
                    if val is None and label is None and idx is None:
                        results.append(
                            {
                                **res,
                                "ok": False,
                                "error": f"'select' action at step {i} requires one of 'value', 'label', 'index'",
                            },
                        )
                        failed = {
                            "ok": False,
                            "error": f"'select' action at step {i} requires one of 'value', 'label', 'index'",
                            "step": i,
                            "completed": results,
                        }
                        break
                    try:
                        open_delay = parse_numeric_unit(act.get("open_delay_s"), 0.5, valid_units=("s", "ms"))
                    except ValueError:
                        res.update(
                            {"ok": False, "error": f"Invalid open_delay_s '{act.get('open_delay_s')}' at step {i}"},
                        )
                        results.append(res)
                        failed = {
                            "ok": False,
                            "error": f"Invalid open_delay_s '{act.get('open_delay_s')}' at step {i}",
                            "step": i,
                            "completed": results,
                        }
                        break
                    sel_res = select_option_with_eval(
                        self.evaluate_runtime,
                        ref,
                        value=val,
                        label=label,
                        index=idx,
                        open_delay_s=open_delay,
                    )

                    if sel_res.get("success"):
                        res.update(
                            {
                                "ok": True,
                                "selected": sel_res.get("selected")
                                or sel_res.get("value")
                                or sel_res.get("text")
                                or ref,
                            },
                        )
                    else:
                        res.update({"ok": False, "error": sel_res.get("error", "Select failed")})

                else:
                    res.update({"ok": False, "error": f"Unknown action type '{act_type}' at step {i}"})
                    results.append(res)
                    failed = {
                        "ok": False,
                        "error": f"Unknown action type '{act_type}' at step {i}",
                        "step": i,
                        "completed": results,
                    }
                    break

                if not res.get("ok"):
                    results.append(res)
                    failed = {
                        "ok": False,
                        "error": res.get("error", f"Action '{act_type}' failed"),
                        "step": i,
                        "completed": results,
                    }
                    break

                results.append(res)
                if clamped_wait_ms > 0 and i < len(actions) - 1:
                    time.sleep(clamped_wait_ms / 1000.0)

            except Exception as exc:
                res.update({"ok": False, "error": str(exc)})
                results.append(res)
                failed = {
                    "ok": False,
                    "error": f"Exception at step {i} ({act_type}): {exc}",
                    "step": i,
                    "completed": results,
                }
                break

        if failed is None:
            self.wait_for_page_stable(timeout_s=1.5)
        if failed is not None:
            return failed
        return {"ok": True, "steps_executed": len(results), "details": results}

    def screenshot_element(self, ref: str, path: str | Path | None = None) -> dict[str, Any]:
        try:
            _, _, obj_id = self._resolve_ref_center(ref, scroll_into_view=False)
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
            return {"ok": False, "error": f"Failed to get box model for {ref}"}

        content = box["result"].get("model", {}).get("content", [])
        if len(content) < 8:
            return {"ok": False, "error": f"Invalid box model dimensions for {ref}"}

        min_x = min(content[0], content[2], content[4], content[6])
        min_y = min(content[1], content[3], content[5], content[7])
        max_x = max(content[0], content[2], content[4], content[6])
        max_y = max(content[1], content[3], content[5], content[7])

        clip = {"x": min_x, "y": min_y, "width": max(1, max_x - min_x), "height": max(1, max_y - min_y), "scale": 1}
        # clip is in CSS pixels relative to the layout viewport; captureBeyondViewport would reinterpret
        # clip in document coords and yield the wrong region on any scrolled page.
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

    def wait_for_download(self, timeout: float = 30.0) -> dict[str, Any]:
        grace_deadline = time.monotonic() + 2.0
        while not self._pending_downloads:
            if time.monotonic() >= grace_deadline:
                return {"ok": False, "error": "no pending download found"}
            time.sleep(0.05)

        with self._state_lock:
            if not self._pending_downloads:
                return {"ok": False, "error": "no pending download found"}
            guid, entry = list(self._pending_downloads.items())[-1]
            event = entry["event"]

        if not event.wait(timeout=timeout):
            with self._state_lock:
                self._pending_downloads.pop(guid, None)
            return {"ok": False, "error": f"download timed out after {timeout}s"}

        with self._state_lock:
            entry = self._pending_downloads.pop(guid, {})
        state = entry.get("state", "unknown")
        if state == "completed":
            return {"ok": True, "filename": entry.get("filename", ""), "guid": guid}
        return {"ok": False, "error": f"download ended with state: {state}"}

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run())
        except BaseException as e:
            if not self._ready_event.is_set():
                self._start_error = e
                self._ready_event.set()
            else:
                logger.warning("CDP supervisor %s crashed: %s", self.task_id, e)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            with contextlib.suppress(Exception):
                loop.close()
            with self._state_lock:
                self._active = False

    async def _run(self) -> None:
        attempt = 0
        backoff = 0.5
        while not self._stop_requested:
            try:
                self._ws = await asyncio.wait_for(
                    websockets.connect(self.cdp_url, max_size=50 * 1024 * 1024),
                    timeout=10.0,
                )
            except Exception as e:
                attempt += 1
                if not self._ready_event.is_set():
                    self._start_error = e
                    self._ready_event.set()
                    return
                logger.warning("CDP supervisor %s connect failed (attempt %s): %s", self.task_id, attempt, e)
                jitter = random.uniform(0.0, min(backoff, _CDP_BACKOFF_MAX))
                await asyncio.sleep(jitter)
                backoff = min(backoff * 2, _CDP_BACKOFF_MAX)
                continue

            reader_task = asyncio.create_task(self._read_loop(), name="cdp-reader")
            try:
                with self._state_lock:
                    self._set_session(page=None, active=None)
                    self._attached_targets.clear()
                    self._child_sessions.clear()

                await self._attach_initial_page()
                with self._state_lock:
                    self._active = True
                backoff = 0.5
                if not self._ready_event.is_set():
                    self._ready_event.set()

                await reader_task
            except BaseException as e:
                if not self._ready_event.is_set():
                    self._start_error = e
                    self._ready_event.set()
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
                ws = self._ws
                self._ws = None
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

            if self._stop_requested:
                return

            jitter = random.uniform(0.0, min(backoff, _CDP_BACKOFF_MAX))
            await asyncio.sleep(jitter)
            backoff = min(backoff * 2, _CDP_BACKOFF_MAX)

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
        self._set_session(root_frame=ft_resp.get("result", {}).get("frameTree", {}).get("frame", {}).get("id", ""))

        await self._cdp("Page.enable", session_id=sid)
        await self._cdp("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=sid)
        await self._cdp("Runtime.enable", session_id=sid)
        await self._cdp("Accessibility.enable", session_id=sid)
        await self._cdp("DOM.enable", session_id=sid)
        await self._cdp(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "eventsEnabled": True, "downloadPath": tempfile.gettempdir()},
            session_id=sid,
        )
        await self._cdp(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
            session_id=sid,
        )
        await self._install_dialog_bridge(sid)

    async def _install_dialog_bridge(self, session_id: str) -> None:
        try:
            await self._cdp(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": _DIALOG_BRIDGE_SCRIPT, "runImmediately": True},
                session_id=session_id,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("dialog bridge script install failed: %s", e)
        try:
            await self._cdp(
                "Fetch.enable",
                {
                    "patterns": [{"urlPattern": DIALOG_BRIDGE_URL_PATTERN, "requestStage": "Request"}],
                    "handleAuthRequests": False,
                },
                session_id=session_id,
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("dialog bridge fetch enable failed: %s", e)

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

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = fut
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_calls.pop(call_id, None)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._stop_requested:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                if "id" in msg:
                    fut = self._pending_calls.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(f"CDP error on id={msg['id']}: {msg['error']}"))
                        else:
                            fut.set_result(msg)
                elif "method" in msg:
                    await self._on_event(msg["method"], msg.get("params", {}), msg.get("sessionId"))
        except Exception as e:
            logger.debug("CDP read loop exited: %s", e)

    async def _on_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Page.frameNavigated":
            self._on_frame_navigated(params, session_id)
            self._dispatch_frame_navigated(params, session_id)
        elif method == "Page.lifecycleEvent":
            self._dispatch_lifecycle(params, session_id)
        elif method == "Page.javascriptDialogOpening":
            await self._on_dialog_opening(params, session_id)
        elif method == "Page.javascriptDialogClosed":
            await self._on_dialog_closed(params, session_id)
        elif method == "Fetch.requestPaused":
            await self._on_fetch_paused(params, session_id)
        elif method == "Page.frameAttached":
            self._on_frame_attached(params, session_id)
        elif method == "Page.frameDetached":
            self._on_frame_detached(params, session_id)
        elif method == "Target.attachedToTarget":
            await self._on_target_attached(params)
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

    def _await_frame_navigated(self, frame_id: str) -> asyncio.Future:
        loop = self._loop
        fut: asyncio.Future = loop.create_future()
        bucket = self._frame_navigated_waiters.setdefault(frame_id, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, fid=frame_id: self._pop_waiter(self._frame_navigated_waiters, fid, f))
        return fut

    def _await_lifecycle(self, frame_id: str, name: str) -> asyncio.Future:
        loop = self._loop
        fut: asyncio.Future = loop.create_future()
        key = (frame_id, name)
        bucket = self._lifecycle_waiters.setdefault(key, [])
        bucket.append(fut)
        fut.add_done_callback(lambda f, k=key: self._pop_waiter(self._lifecycle_waiters, k, f))
        return fut

    def _pop_waiter(self, store: dict[Any, list[asyncio.Future]], key: Any, fut: asyncio.Future) -> None:
        bucket = store.get(key)
        if bucket is None:
            return
        if fut in bucket:
            bucket.remove(fut)
        if not bucket:
            store.pop(key, None)

    def _dispatch_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frame", {}).get("id", "")
        waiters = self._frame_navigated_waiters.pop(fid, ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)

    def _dispatch_lifecycle(self, params: dict[str, Any], _session_id: str | None) -> None:
        name = params.get("name", "")
        fid = params.get("frameId", "")
        waiters = self._lifecycle_waiters.pop((fid, name), ()) or ()
        for fut in waiters:
            if not fut.done():
                fut.set_result(params)

    def _on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame", {})
        fid = frame.get("id", "")
        url = frame.get("url", "") or ""
        if fid:
            parent_id = frame.get("parentId")
            # 先原子取 sids 再进入 state_lock，避免跨锁读 sids 后又持 _state_lock。
            sids = self._current_sids()
            with self._state_lock:
                # Only adopt as the main root if no root is set yet OR the existing root
                # belongs to the same page session. Popup windows opened via window.open()
                # also have parentId unset and would otherwise clobber the main root.
                is_page_session = session_id == sids.page
                if not parent_id and (not sids.root_frame or is_page_session):
                    self._set_session(root_frame=fid)
                sids = self._current_sids()
                self._frames[fid] = FrameInfo(
                    frame_id=fid,
                    url=url,
                    origin=frame.get("securityOrigin", ""),
                    parent_frame_id=parent_id,
                    is_oopif=session_id != sids.page and session_id is not None,
                    cdp_session_id=session_id,
                    name=frame.get("name", ""),
                )
        # Root frame 每次导航（包括同 URL reload）都使 _root_doc_generation 自增；
        # 与 refs 缓存记录的 _refs_doc_generation 不一致即清空 ref。子/iframe 不影响主页面 ref。
        # 必须在 _state_lock 释放前捕获 root_frame 快照：释放后任何 set_active_session_id /
        # 其它 _on_frame_navigated 都可能改写 _session_ids.root_frame，造成 TOCTOU 误判。
        is_root = not frame.get("parentId")
        with self._state_lock:
            current_root_frame = self._current_sids().root_frame
        is_main_root = is_root and fid == current_root_frame
        self._refs.note_navigation(url, is_main_root=is_main_root)

    def _on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        pid = params.get("parentFrameId")
        if fid:
            with self._state_lock:
                self._frames[fid] = FrameInfo(
                    frame_id=fid,
                    url="",
                    origin="",
                    parent_frame_id=pid,
                    is_oopif=False,
                    cdp_session_id=session_id,
                )

    def _on_frame_detached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        if fid:
            with self._state_lock:
                self._frames.pop(fid, None)

    async def _on_target_attached(self, params: dict[str, Any]) -> None:
        target_info = params.get("targetInfo", {})
        session_id = params.get("sessionId", "")
        target_id = target_info.get("targetId", "")
        target_type = target_info.get("type", "")

        if target_type == "page" and target_id and session_id:
            with self._state_lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": target_info.get("title", "")}
        elif target_type == "iframe" and session_id:
            with self._state_lock:
                self._child_sessions[target_id] = session_id

    def _on_target_detached(self, params: dict[str, Any]) -> None:
        target_id = params.get("targetId", "")
        with self._state_lock:
            self._attached_targets.pop(target_id, None)
            self._child_sessions.pop(target_id, None)

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
                "filename": params.get("suggestedFilename", ""),
                "url": params.get("url", ""),
                "event": threading.Event(),
            }

    def _on_download_progress(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        state = params.get("state", "")
        with self._state_lock:
            entry = self._pending_downloads.get(guid)
            if entry is not None:
                entry["state"] = state
                if state in ("completed", "canceled"):
                    entry["event"].set()

    async def _on_dialog_opening(self, params: dict[str, Any], session_id: str | None) -> None:
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

    async def _on_dialog_closed(self, params: dict[str, Any], session_id: str | None) -> None:
        self._dialog_manager.on_remote_closed(session_id)

    async def _on_fetch_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        url = str(params.get("request", {}).get("url") or "")
        request_id = params.get("requestId")
        if not request_id:
            return

        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        dialog = PendingDialog(
            id=self._dialog_manager.next_id(),
            type=qs.get("kind", ["alert"])[0],
            message=qs.get("message", [""])[0],
            default_prompt=qs.get("default_prompt", [""])[0],
            opened_at=time.time(),
            cdp_session_id=session_id or self._current_sids().page or "",
            bridge_request_id=request_id,
        )
        self._dialog_manager.open(dialog)

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


class SupervisorRegistry:
    def __init__(self) -> None:
        self._supervisors: dict[str, CDPSupervisor] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> CDPSupervisor | None:
        with self._lock:
            return self._supervisors.get(task_id)

    def get_or_start(
        self,
        task_id: str,
        cdp_url: str,
        *,
        launch_handle: Any = None,
        auto_owned: bool = False,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        timeout: float = 15.0,
    ) -> CDPSupervisor:
        # 先在锁外确认现有 supervisor 的活性, 避免对正在运行的实例误判为已死。
        # 锁内只做 dict 替换 + 必要时的 stop() 抢占, 不在锁内调阻塞 start().
        existing = self._supervisors.get(task_id)
        if existing is not None and existing._active:
            return existing

        stale = existing
        sup = CDPSupervisor(
            task_id=task_id,
            cdp_url=cdp_url,
            launch_handle=launch_handle,
            auto_owned=auto_owned,
            dialog_policy=dialog_policy,
            dialog_timeout_s=dialog_timeout_s,
        )
        with self._lock:
            # 重新确认: 拿到锁前可能已有别的线程把活的塞回去。
            live = self._supervisors.get(task_id)
            if live is not None and live._active:
                return live
            self._supervisors[task_id] = sup

        # 锁外先回收 stale 实例, 再启动新的 — 防止旧 WS / Chromium 进程泄漏。
        if stale is not None:
            try:
                stale.stop()
            except Exception as e:
                logger.debug("Error stopping stale supervisor %s: %s", task_id, e)

        sup.start(timeout=timeout)
        return sup

    def stop(self, task_id: str) -> None:
        with self._lock:
            sup = self._supervisors.pop(task_id, None)
        if sup is not None:
            sup.stop()

    def stop_all(self) -> None:
        with self._lock:
            sups = list(self._supervisors.values())
            self._supervisors.clear()
        for sup in sups:
            try:
                sup.stop()
            except Exception as e:
                logger.debug("Error stopping supervisor %s: %s", sup.task_id, e)


SUPERVISOR_REGISTRY = SupervisorRegistry()
