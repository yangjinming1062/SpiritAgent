import asyncio
import base64
import contextlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from mcp import ClientSession, McpError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CONNECTION_CLOSED
from utils import IS_MACOS, cfg_get, get_spiritagent_home, is_env_passthrough, load_config, safe_schedule_threadsafe

from .cu_backend import DESKTOP_SENTINELS, ActionResult, CaptureResult, ComputerUseBackend, UIElement

logger = logging.getLogger(__name__)

_CUA_DRIVER_EXE = "cua-driver"
_CUA_DRIVER_ARGS = ["mcp"]
_START_TIMEOUT_S = 15.0
_STOP_TIMEOUT_S = 5.0
_CALL_TIMEOUT_S = 30.0

_MACOS_SHELL_APP_NAMES = frozenset({"finder", "dock"})
_MODIFIER_KEYS = frozenset({"cmd", "shift", "option", "ctrl", "fn"})

# 子进程不继承 JWT/Backend URL/safeStorage 密文。
_CUA_DRIVER_SAFE_ENV_EXACT = frozenset(
    {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TMPDIR"},
)
_CUA_DRIVER_SAFE_ENV_PREFIXES = ("LC_", "XDG_", "DYLD_")
_CUA_DRIVER_SECRET_SUBSTRINGS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "PASSWD",
    "JWT",
    "WEBHOOK",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)

_driver_verified = False


class _Window(TypedDict):
    app_name: str
    pid: int
    window_id: int
    title: str


def _is_cua_secret_var(name: str) -> bool:
    """名字含强密钥特征，或以 ``_KEY`` 作词尾（STRIPE_KEY）的变量视为凭据；KEYBOARD_LAYOUT 等不受影响。"""
    upper = name.upper()
    return any(s in upper for s in _CUA_DRIVER_SECRET_SUBSTRINGS) or bool(re.search(r"_KEY(?:_|$)", upper))


def _cua_driver_command() -> str:
    """``$SPIRITAGENT_HOME/bin`` 手动安装 > runner 依赖 wheel 内置二进制 > PATH 裸名。"""
    managed = get_spiritagent_home() / "bin" / _CUA_DRIVER_EXE
    if managed.is_file():
        return str(managed)
    if (spec := importlib.util.find_spec("cua_driver")) is not None:
        for location in spec.submodule_search_locations or ():
            if (wheel_bin := Path(location) / "bin" / _CUA_DRIVER_EXE).is_file():
                return str(wheel_bin)
    return _CUA_DRIVER_EXE


def cua_driver_binary_available() -> bool:
    """实际跑 --version 确认可执行；只缓存成功结果。"""
    global _driver_verified
    if _driver_verified:
        return True
    if not (path := shutil.which(_cua_driver_command())):
        return False
    try:
        result = subprocess.run([path, "--version"], capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("cua-driver probe failed: %s", e)
        return False
    _driver_verified = result.returncode == 0
    return _driver_verified


def _build_cua_driver_env() -> dict[str, str]:
    """cua-driver 子进程环境：白名单变量加 terminal.env_passthrough 配置的变量，剔除名含凭据特征的变量。"""
    safe = {
        key: value
        for key, value in os.environ.items()
        if not _is_cua_secret_var(key)
        and (
            key.upper() in _CUA_DRIVER_SAFE_ENV_EXACT
            or key.upper().startswith(_CUA_DRIVER_SAFE_ENV_PREFIXES)
            or is_env_passthrough(key)
        )
    }
    telemetry_enabled = bool(cfg_get(load_config(), "computer_use", "cua_telemetry", default=False))
    safe["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "1" if telemetry_enabled else "0"
    return safe


def _parse_elements(
    raw_elements: list[Any],
    snapshot_id: Any,
    window_id: int,
) -> tuple[list[UIElement], dict[int, dict[str, Any]]]:
    """寻址优先 element_token，否则 element_index+snapshot_id+window_id。"""
    elements: list[UIElement] = []
    refs: dict[int, dict[str, Any]] = {}
    for raw in raw_elements:
        if not isinstance(raw, dict) or not isinstance(idx := raw.get("element_index"), int):
            continue
        if isinstance(token := raw.get("element_token"), str) and token:
            refs[idx] = {"element_token": token}
        elif isinstance(snapshot_id, str) and snapshot_id:
            refs[idx] = {"element_index": idx, "snapshot_id": snapshot_id, "window_id": window_id}
        else:
            continue
        frame = raw.get("frame")
        bounds = (0, 0, 0, 0)
        if isinstance(frame, dict):
            with contextlib.suppress(KeyError, TypeError, ValueError):
                bounds = (int(frame["x"]), int(frame["y"]), int(frame["w"]), int(frame["h"]))
        elements.append(
            UIElement(index=idx, role=str(raw.get("role") or ""), label=str(raw.get("label") or ""), bounds=bounds),
        )
    return elements, refs


def _image_dimensions_from_bytes(raw: bytes) -> tuple[int, int]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if w > 0 and h > 0:
            return w, h
    if raw.startswith(b"\xff\xd8"):
        i, n = 2, len(raw)
        while i + 9 < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, i = raw[i + 1], i + 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n or (seg_len := int.from_bytes(raw[i : i + 2], "big")) < 2 or i + seg_len > n:
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if (
                    seg_len >= 7
                    and (w := int.from_bytes(raw[i + 5 : i + 7], "big")) > 0
                    and (h := int.from_bytes(raw[i + 3 : i + 5], "big")) > 0
                ):
                    return w, h
                break
            i += seg_len
    return 0, 0


def _extract_tool_result(mcp_result: Any) -> dict[str, Any]:
    images: list[tuple[str, str]] = []
    text_chunks: list[str] = []
    for part in getattr(mcp_result, "content", None) or []:
        ptype = getattr(part, "type", None)
        if ptype == "image" and getattr(part, "data", None):
            images.append((part.data, getattr(part, "mimeType", None) or "image/png"))
        elif ptype == "text" and getattr(part, "text", ""):
            text_chunks.append(part.text)
    data: Any = None
    if text_chunks:
        joined = "\n".join(text_chunks)
        try:
            data = json.loads(joined) if joined.strip().startswith(("{", "[")) else joined
        except json.JSONDecodeError:
            data = joined
    return {
        "data": data,
        "images": images,
        "structuredContent": getattr(mcp_result, "structuredContent", None) or {},
        "isError": bool(getattr(mcp_result, "isError", False)),
    }


def _result_message(out: dict[str, Any]) -> str:
    data = out["data"]
    if isinstance(data, dict):
        return str(data.get("message") or data.get("error") or "")
    return data if isinstance(data, str) else ""


def _is_closed_session_error(exc: BaseException) -> bool:
    if isinstance(exc, McpError):
        return exc.error.code == CONNECTION_CLOSED and exc.error.message == "Connection closed"
    # 按类名识别 anyio 流关闭异常，不直接依赖传递依赖。
    return type(exc).__name__ in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"} or isinstance(
        exc,
        BrokenPipeError | EOFError,
    )


class _AsyncBridge:
    """在专属线程运行事件循环，供同步工具线程调用 MCP 协程。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._loop = loop
        self._thread = threading.Thread(target=_run, daemon=True, name="cua-driver-loop")
        self._thread.start()

    def run[T](self, coro: Coroutine[Any, Any, T], timeout: float) -> T:
        """超时即取消协程并抛 TimeoutError，避免请求或子进程在后台继续。"""
        alive = self._thread is not None and self._thread.is_alive()
        if not alive or (fut := safe_schedule_threadsafe(coro, self._loop)) is None:
            coro.close()
            raise RuntimeError("cua-driver event loop is not running")
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            fut.cancel()
            raise TimeoutError(f"cua-driver did not respond within {timeout:.0f}s") from None

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        self._thread = self._loop = None
        if loop is None or thread is None or not thread.is_alive():
            return
        # 先取消残留任务再停循环。
        if (fut := safe_schedule_threadsafe(_cancel_pending_tasks(), loop)) is not None:
            try:
                fut.result(timeout=_STOP_TIMEOUT_S)
            except Exception as e:
                logger.warning("cua-driver pending tasks did not finish: %s", e)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)


async def _cancel_pending_tasks() -> None:
    current = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class _SessionHost:
    session: ClientSession
    stop: asyncio.Event
    task: asyncio.Task[None]


async def _hold_session(ready: asyncio.Future[ClientSession], stop: asyncio.Event) -> None:
    # anyio 要求同一任务内退出；stdio_client 关 stdin 后再终止子进程。
    params = StdioServerParameters(command=_cua_driver_command(), args=_CUA_DRIVER_ARGS, env=_build_cua_driver_env())
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            if ready.done():
                return
            ready.set_result(session)
            await stop.wait()
    except Exception as e:
        # ExceptionGroup 取根因。
        while isinstance(e, ExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        if not ready.done():
            ready.set_exception(e)
        else:
            logger.warning("cua-driver session ended: %s", e)
    finally:
        if not ready.done():
            ready.cancel()


async def _open_session() -> _SessionHost:
    ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()
    task = asyncio.create_task(_hold_session(ready, stop), name="cua-driver-session")
    try:
        session = await ready
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    return _SessionHost(session=session, stop=stop, task=task)


async def _close_session(host: _SessionHost) -> None:
    host.stop.set()
    await host.task


async def _call_tool(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return _extract_tool_result(await session.call_tool(name, args))


class _CuaDriverSession:
    """cua-driver MCP 会话：按需启动；连接断开后下一次调用重建会话。"""

    def __init__(self, bridge: _AsyncBridge) -> None:
        self._bridge = bridge
        self._lock = threading.Lock()
        self._host: _SessionHost | None = None

    def _current(self) -> _SessionHost:
        with self._lock:
            if self._host is not None and self._host.task.done():
                self._host = None
            if self._host is None:
                self._bridge.start()
                self._host = self._bridge.run(_open_session(), timeout=_START_TIMEOUT_S)
            return self._host

    def _discard(self, host: _SessionHost) -> None:
        with self._lock:
            if self._host is not host:
                return
            self._host = None
        try:
            self._bridge.run(_close_session(host), timeout=_STOP_TIMEOUT_S)
        except Exception as e:
            logger.warning("cua-driver session cleanup failed: %s", e)

    def start(self) -> None:
        self._current()

    def stop(self) -> None:
        with self._lock:
            host, self._host = self._host, None
        if host is not None:
            self._bridge.run(_close_session(host), timeout=_STOP_TIMEOUT_S)

    def call_tool(self, name: str, args: dict[str, Any], *, retry_on_disconnect: bool) -> dict[str, Any]:
        """``retry_on_disconnect`` 只用于只读查询：输入动作可能已送达，断线后重发会重复执行。"""
        host = self._current()
        try:
            return self._bridge.run(_call_tool(host.session, name, args), timeout=_CALL_TIMEOUT_S)
        except Exception as e:
            if not _is_closed_session_error(e):
                raise
            self._discard(host)
            if not retry_on_disconnect:
                raise RuntimeError(
                    f"the connection to the desktop driver was lost during {name}; the action may or may not "
                    "have taken effect, capture again before retrying",
                ) from e
            logger.warning("cua-driver MCP session closed during %s; reconnecting once", name)
        return self._bridge.run(_call_tool(self._current().session, name, args), timeout=_CALL_TIMEOUT_S)


class CuaDriverBackend(ComputerUseBackend):
    """macOS 后端：经 cua-driver 按 pid / window_id 向目标窗口投递输入，坐标为窗口截图像素。"""

    def __init__(self) -> None:
        self._bridge = _AsyncBridge()
        self._session = _CuaDriverSession(self._bridge)
        # 锁保证 pid/window_id 同次选择。
        self._state_lock = threading.Lock()
        self._target: _Window | None = None
        # 新快照使旧 token 失效。
        self._element_refs: dict[int, dict[str, Any]] = {}

    def start(self) -> None:
        self._session.start()

    def stop(self) -> None:
        try:
            self._session.stop()
        finally:
            self._bridge.stop()

    def is_available(self) -> bool:
        return IS_MACOS and cua_driver_binary_available()

    def _query(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        out = self._session.call_tool(name, args, retry_on_disconnect=True)
        if out["isError"]:
            raise RuntimeError(f"cua-driver {name} failed: {_result_message(out) or 'no details'}")
        return out

    def _list_windows(self) -> list[_Window]:
        """屏幕上的窗口，最前面的在前。"""
        raw = self._query("list_windows", {"on_screen_only": True})["structuredContent"].get("windows")
        if not isinstance(raw, list):
            raise RuntimeError("cua-driver list_windows returned no window list")
        # z_index 大者靠前；null 排最后。
        ordered = sorted(
            (w for w in raw if isinstance(w, dict)),
            key=lambda w: w["z_index"] if isinstance(w.get("z_index"), int) else float("-inf"),
            reverse=True,
        )
        return [
            _Window(
                app_name=str(w.get("app_name") or ""),
                pid=int(w["pid"]),
                window_id=int(w["window_id"]),
                title=str(w.get("title") or ""),
            )
            for w in ordered
        ]

    def _match_windows(self, app: str) -> list[_Window]:
        needle = app.lower()
        return [w for w in self._list_windows() if needle in w["app_name"].lower()]

    def _current_target(self) -> _Window | None:
        with self._state_lock:
            return self._target

    def _set_target(self, target: _Window) -> None:
        with self._state_lock:
            if self._target != target:
                self._element_refs = {}
            self._target = target

    def _element_ref(self, element: int) -> dict[str, Any] | None:
        with self._state_lock:
            return self._element_refs.get(element)

    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        if app and app.lower() in DESKTOP_SENTINELS:
            windows = [w for w in self._list_windows() if w["app_name"].lower() in _MACOS_SHELL_APP_NAMES]
            if not windows:
                raise LookupError(f"no Finder or Dock window is visible for app={app!r}")
        elif app:
            if not (windows := self._match_windows(app)):
                raise LookupError(
                    f"no on-screen window matched app={app!r}; call list_apps to see app names "
                    "(macOS may report localized names)",
                )
        elif not (windows := self._list_windows()):
            raise LookupError("no on-screen window to capture")
        self._set_target(windows[0])
        return self._capture_window(windows[0], mode)

    def recapture(self, mode: str = "som") -> CaptureResult:
        if (target := self._current_target()) is None:
            raise LookupError("no window has been captured yet; call capture first")
        return self._capture_window(target, mode)

    def _capture_window(self, target: _Window, mode: str) -> CaptureResult:
        # get_window_state 返回元素树+截图；每次调用刷新寻址参数。
        args: dict[str, Any] = {"pid": target["pid"], "window_id": target["window_id"]}
        if mode == "ax":
            args["include_screenshot"] = False
        out = self._query("get_window_state", args)
        structured = out["structuredContent"]
        raw_elements = structured.get("elements")
        elements, refs = (
            _parse_elements(raw_elements, structured.get("snapshot_id"), target["window_id"])
            if isinstance(raw_elements, list)
            else ([], {})
        )
        with self._state_lock:
            if self._target == target:
                self._element_refs = refs
        capture = CaptureResult(
            mode=mode,
            width=0,
            height=0,
            elements=elements if mode != "vision" else [],
            app=target["app_name"],
            window_title=target["title"],
            note=str(structured.get("degraded_reason") or ""),
        )
        if mode != "ax" and out["images"]:
            capture.png_b64, capture.image_mime_type = out["images"][0]
            capture.width, capture.height = _image_dimensions_from_bytes(base64.b64decode(capture.png_b64))
        return capture

    def _action(self, name: str, args: dict[str, Any]) -> ActionResult:
        try:
            out = self._session.call_tool(name, args, retry_on_disconnect=False)
        except TimeoutError as e:
            return ActionResult(
                ok=False,
                action=name,
                message=f"{e}; the action may still take effect, capture again before retrying.",
            )
        except Exception as e:
            logger.warning("cua-driver %s call failed: %s", name, e)
            return ActionResult(ok=False, action=name, message=f"cua-driver error: {e}")
        data = out["data"]
        return ActionResult(
            ok=not out["isError"],
            action=name,
            message=_result_message(out),
            meta=data if isinstance(data, dict) else {},
        )

    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        if (target := self._current_target()) is None:
            return _no_target("click")
        if click_count == 2 and button != "left":
            return ActionResult(
                ok=False,
                action="click",
                message="Double-click is only supported with the left button.",
            )
        tool = "double_click" if click_count == 2 else "right_click" if button == "right" else "click"
        args: dict[str, Any] = {"pid": target["pid"]}
        if element is not None:
            if (ref := self._element_ref(element)) is None:
                return _unknown_element(tool, element)
            args |= ref
        elif x is not None and y is not None:
            args |= {"window_id": target["window_id"], "x": x, "y": y}
        else:
            return ActionResult(ok=False, action=tool, message="click requires element or coordinate.")
        if button == "middle":
            args["button"] = "middle"
        if modifiers:
            args["modifier"] = modifiers
        return self._action(tool, args)

    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        # macOS 仅前台拖拽，本后端不启用。
        return ActionResult(ok=False, action="drag", message="Dragging is not supported on macOS.")

    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
    ) -> ActionResult:
        if (target := self._current_target()) is None:
            return _no_target("scroll")
        if modifiers:
            return ActionResult(
                ok=False,
                action="scroll",
                message="Modifier keys are not supported for scrolling on macOS.",
            )
        args: dict[str, Any] = {"pid": target["pid"], "direction": direction, "amount": max(1, min(50, amount))}
        if element is not None:
            if (ref := self._element_ref(element)) is None:
                return _unknown_element("scroll", element)
            args |= ref
        else:
            args["window_id"] = target["window_id"]
            if x is not None and y is not None:
                args |= {"x": x, "y": y}
        return self._action("scroll", args)

    def type_text(self, text: str) -> ActionResult:
        if (target := self._current_target()) is None:
            return _no_target("type")
        return self._action("type_text", {"pid": target["pid"], "text": text})

    def key(self, keys: list[str]) -> ActionResult:
        if (target := self._current_target()) is None:
            return _no_target("key")
        modifiers = [k for k in keys if k in _MODIFIER_KEYS]
        if len(others := [k for k in keys if k not in _MODIFIER_KEYS]) != 1:
            return ActionResult(
                ok=False,
                action="key",
                message=f"A key combo needs exactly one non-modifier key: {keys}.",
            )
        res = (
            self._action("hotkey", {"pid": target["pid"], "keys": [*modifiers, others[0]]})
            if modifiers
            else self._action("press_key", {"pid": target["pid"], "key": others[0]})
        )
        res.action = "key"
        return res

    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        if (target := self._current_target()) is None:
            return _no_target("set_value")
        if element is None:
            return ActionResult(ok=False, action="set_value", message="set_value requires element.")
        if (ref := self._element_ref(element)) is None:
            return _unknown_element("set_value", element)
        return self._action("set_value", {"pid": target["pid"], **ref, "value": value})

    def list_apps(self) -> list[dict[str, Any]]:
        data = self._query("list_apps", {})["data"]
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("apps", [])
        if isinstance(data, str):
            return [
                {"name": m.group(1).strip(), "pid": int(m.group(2))}
                for line in data.splitlines()
                if (m := re.search(r"(.+?)\s+\(pid\s+(\d+)\)", line))
            ]
        return []

    def focus_app(self, app: str, bring_to_front: bool = False) -> ActionResult:
        if not (windows := self._match_windows(app)):
            return ActionResult(ok=False, action="focus_app", message=f"No on-screen window found for app '{app}'.")
        target = windows[0]
        self._set_target(target)
        # 按 window_id 投递，不抢前台。
        suffix = (
            "Raising windows is not supported on macOS; input is sent to the window in the background."
            if bring_to_front
            else "Input is sent to the window in the background without raising it."
        )
        return ActionResult(
            ok=True,
            action="focus_app",
            message=f"Targeted {target['app_name']} (pid {target['pid']}, window {target['window_id']}). {suffix}",
        )


def _no_target(action: str) -> ActionResult:
    return ActionResult(ok=False, action=action, message="No target window; call capture or focus_app first.")


def _unknown_element(action: str, element: int) -> ActionResult:
    return ActionResult(
        ok=False,
        action=action,
        message=f"Element {element} is not in the latest capture of the target window; capture again.",
    )
