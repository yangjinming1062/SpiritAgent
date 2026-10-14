import enum
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from spirit_agent_runner.features.browser.refs import SessionIds

CONSOLE_HISTORY_MAX = 50
PAGE_DOMAINS: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("Page.enable", None),
    ("Page.setLifecycleEventsEnabled", {"enabled": True}),
    ("Runtime.enable", None),
    ("Accessibility.enable", None),
    ("DOM.enable", None),
)


class _Unset(enum.Enum):
    UNSET = enum.auto()


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


_UNSET: Final = _Unset.UNSET


class PageState:
    """Own selected page identity, attached tabs, frames and console history."""

    def __init__(self, send_cdp: Callable[..., dict[str, Any]], note_navigation: Callable[[], None]) -> None:
        self.send_cdp = send_cdp
        self._note_navigation = note_navigation
        self._lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session_ids = SessionIds(active=None, page=None, root_frame="")
        self._attached_targets: dict[str, dict[str, str]] = {}
        self._frames: dict[str, FrameInfo] = {}
        self._console_events: list[ConsoleEvent] = []

    def reset_connection(self) -> None:
        with self._lock:
            self.set_session(page=None, active=None)
            self._attached_targets.clear()

    def record_attachment(self, target_id: str, session_id: str, frame_id: str = "") -> None:
        with self._lock:
            self._attached_targets[target_id] = {"session_id": session_id, "title": "", "frame_id": frame_id}

    def frame_tree(self) -> dict[str, Any]:
        with self._lock:
            return self._frame_tree_locked()

    def record_root_frame(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self.set_session(root_frame=frame["id"])
            self._record_root_frame_locked(frame)

    def _frame_tree_locked(self) -> dict[str, Any]:
        frames = list(self._frames.values())
        root = self._frames.get(self.current_session().root_frame)
        if root is None:
            return {"frames_count": len(frames)}
        return {"root": root.to_dict(), "frames_count": len(frames)}

    def activate_tab_session(self, session_id: str) -> dict[str, Any]:
        """切到新 page session：启用事件域并跟 root_frame，否则导航等待器空等超时。"""
        for method, params in PAGE_DOMAINS:
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
        with self._lock:
            sids = self.current_session()
            changed = (sids.active or sids.page) != session_id
            self.set_session(active=session_id, page=sids.page or session_id, root_frame=root)
            self._record_root_frame_locked(frame)
            for info in self._attached_targets.values():
                if info.get("session_id") == session_id:
                    info["frame_id"] = root
        if changed:
            self._note_navigation()
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
        if (sids := self.current_session()).active or sids.page:
            return
        targets = self.list_tabs()
        if not targets.get("ok"):
            raise RuntimeError(targets.get("error", "Failed to list browser tabs"))
        candidates = targets.get("result", {}).get("targetInfos", [])
        attached: dict[str, Any] | None = None
        for page in candidates:
            if page.get("type") != "page" or not (target_id := page.get("targetId")):
                continue
            result = self.attach_target(target_id)
            # Edge exposes transient utility pages such as downloads-hub as targets,
            # but they cannot be attached. Closed tabs may also disappear between calls.
            if not result.get("ok") and "No target with given id found" in result.get("error", ""):
                continue
            attached = result
            break
        if attached is None:
            created = self.send_cdp("Target.createTarget", {"url": "about:blank"})
            if not created.get("ok") and "Failed to open a new tab" in created.get("error", ""):
                created = self.send_cdp("Target.createTarget", {"url": "about:blank", "newWindow": True})
            if not created.get("ok"):
                raise RuntimeError(created.get("error", "Failed to create a browser tab"))
            target_id = created.get("result", {}).get("targetId")
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
        with self._lock:
            return self.current_session().active, dict(self._attached_targets)

    def current_session(self) -> SessionIds:
        """原子读取会话标识三元组。单属性读取在 CPython 下原子，无需持锁。"""
        return self._session_ids

    def set_session(
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
            with self._lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": ""}
        return result

    def close_tab(self, tab_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if tab_id is None:
                for tid, info in self._attached_targets.items():
                    if info.get("session_id") == self.current_session().active:
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
        fallback = self.forget_target(tab_id, closed_session)
        if fallback is not None:
            # root_frame 须跟回退会话。
            ft = self.send_cdp("Page.getFrameTree", session_id=fallback)
            if ft.get("ok"):
                with self._lock:
                    if self.current_session().active == fallback:
                        frame = ft["result"].get("frameTree", {}).get("frame", {})
                        if root := frame.get("id"):
                            self.set_session(root_frame=root)
                            self._record_root_frame_locked(frame)
        return {"ok": True, "tab_id": tab_id}

    def forget_target(self, target_id: str | None, session_id: str | None) -> str | None:
        """移除关闭或断开的页会话，并把当前目标切到仍存活的已附着页。"""
        with self._lock:
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
            sids = self.current_session()
            remaining = {info["session_id"]: info for info in self._attached_targets.values()}
            fallback = sids.active if sids.active in remaining else next(iter(remaining), None)
            active = fallback if sids.active in removed_ids else sids.active
            page = fallback if sids.page in removed_ids else sids.page
            selected = active or page
            changed = selected != (sids.active or sids.page)
            root = remaining.get(selected, {}).get("frame_id", "") if changed else sids.root_frame
            self.set_session(active=active, page=page, root_frame=root)
        if changed:
            self._note_navigation()
        return selected if changed else None

    def console_messages(self, *, clear: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            events = [{"ts": e.ts, "level": e.level, "text": e.text, "url": e.url} for e in self._console_events]
            if clear:
                self._console_events.clear()
        return events

    def on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame", {})
        fid = frame.get("id", "")
        if not fid:
            return
        parent_id = frame.get("parentId")
        with self._lock:
            sids = self.current_session()
            selected = sids.active or sids.page
            if not parent_id and session_id == selected:
                self.set_session(root_frame=fid)
                sids = self.current_session()
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
            self._note_navigation()

    def on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        fid = params.get("frameId", "")
        pid = params.get("parentFrameId")
        if fid:
            with self._lock:
                self._frames[fid] = FrameInfo(frame_id=fid, url="", origin="", parent_frame_id=pid, is_oopif=False)

    def on_frame_detached(self, params: dict[str, Any]) -> None:
        fid = params.get("frameId", "")
        if fid:
            with self._lock:
                self._frames.pop(fid, None)

    def on_target_attached(self, params: dict[str, Any]) -> None:
        target_info = params.get("targetInfo", {})
        session_id = params.get("sessionId", "")
        target_id = target_info.get("targetId", "")
        if target_info.get("type") == "page" and target_id and session_id:
            with self._lock:
                self._attached_targets[target_id] = {"session_id": session_id, "title": target_info.get("title", "")}

    def on_target_detached(self, params: dict[str, Any]) -> None:
        self.forget_target(params.get("targetId"), params.get("sessionId"))

    def on_console(self, params: dict[str, Any], level_from: str) -> None:
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

        with self._lock:
            self._console_events.append(event)
            if len(self._console_events) > CONSOLE_HISTORY_MAX:
                self._console_events.pop(0)
