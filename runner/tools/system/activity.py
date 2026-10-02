import logging
import subprocess
import time
import uuid
from typing import Any

import psutil
from pydantic import BaseModel, Field
from utils import IS_MACOS, IS_WINDOWS

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # 私有 DLL 实例；HWND 按指针宽传递。
    _user32 = ctypes.WinDLL("user32")
    _kernel32 = ctypes.WinDLL("kernel32")
    _dwmapi = ctypes.WinDLL("dwmapi")
    for _name, _argtypes, _restype in (
        ("GetForegroundWindow", [], wintypes.HWND),
        ("GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
        ("IsIconic", [wintypes.HWND], wintypes.BOOL),
        ("GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
        ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        ("MonitorFromWindow", [wintypes.HWND, wintypes.DWORD], wintypes.HMONITOR),
        ("GetMonitorInfoW", [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)], wintypes.BOOL),
        ("GetGUIThreadInfo", [wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO)], wintypes.BOOL),
        ("EnumWindows", [_WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
        ("GetLastInputInfo", [ctypes.POINTER(_LASTINPUTINFO)], wintypes.BOOL),
        ("GetCursorPos", [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        ("SetCursorPos", [ctypes.c_int, ctypes.c_int], wintypes.BOOL),
        ("SystemParametersInfoW", [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL),
    ):
        _fn = getattr(_user32, _name)
        _fn.argtypes, _fn.restype = _argtypes, _restype
    _kernel32.GetTickCount.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

if IS_MACOS:
    import Quartz
    from AppKit import NSScreen, NSWorkspace

logger = logging.getLogger(__name__)
_WINDOW_SCENE_INSTANCE_ID = uuid.uuid4().hex

_SHELL_WINDOW_CLASSES = frozenset(("Shell_TrayWnd", "WorkerW", "Progman"))
_GW_HWNDNEXT = 2
_GA_ROOT = 2
_MONITOR_DEFAULTTONEAREST = 0x00000002
_SPI_GETWORKAREA = 0x0030
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class WindowInfo(BaseModel):
    title: str
    name: str
    x: int
    y: int
    w: int
    h: int
    focused: bool
    visible: bool
    window_id: str
    pid: int
    z_order: int


class WindowScene(BaseModel):
    windows: list[WindowInfo] = Field(default_factory=list)
    runner_instance_id: str | None = None


def get_idle_seconds() -> float:
    """自上次用户输入以来的秒数；不可用时返回 ``-1.0``。"""
    if IS_WINDOWS:
        return _idle_windows()
    if IS_MACOS:
        return _idle_macos()
    return -1.0


def is_screen_locked() -> bool:
    """会话已锁屏时为 True；无法判断时返回 False（误报"已锁"比漏报更糟）。"""
    if IS_WINDOWS:
        return _locked_windows()
    if IS_MACOS:
        return _locked_macos()
    return False


def get_focused_app() -> dict[str, Any]:
    """前台应用及其窗口；无法判断时返回 ``{}``。"""
    if IS_WINDOWS:
        return _focus_windows()
    if IS_MACOS:
        return _focus_macos()
    return {}


def is_fullscreen() -> bool:
    """前台窗口覆盖其整个显示器时为 True；未知时返回 False。"""
    if IS_WINDOWS:
        return _fullscreen_windows()
    if IS_MACOS:
        return _fullscreen_macos()
    return False


def get_power_state() -> dict[str, bool]:
    """``{on_battery, charging}``；没有电池或供电状态未知时两者均为 False。"""
    battery = psutil.sensors_battery()
    if battery is None or battery.power_plugged is None:
        return {"on_battery": False, "charging": False}
    return {"on_battery": not battery.power_plugged, "charging": battery.power_plugged and battery.percent < 100}


def get_windows() -> WindowScene:
    """可见顶层窗口快照；探测失败时不提供 Runner 实例标识。"""
    if IS_WINDOWS:
        return _windows_windows()
    if IS_MACOS:
        return _windows_macos()
    return WindowScene()


# 拦 cmd 元字符与前导 -/ 被当成选项。
_APP_NAME_FORBIDDEN_CHARS = frozenset("&|<>^\"'%$();{}\n\r\t")


def _sanitize_app_name(name: str) -> str | None:
    stripped = name.strip()
    if not stripped or stripped.startswith("-") or (IS_WINDOWS and stripped.startswith("/")):
        return None
    if any(ch in _APP_NAME_FORBIDDEN_CHARS for ch in stripped):
        return None
    return stripped


def open_application(name: str) -> dict[str, Any]:
    """启动 *name*（可执行名 / 应用名 / 路径），返回 ``{opened, name}`` 或 ``{opened: false, error}``。"""
    if not name.strip():
        return {"opened": False, "error": "application name is required"}
    if (safe_name := _sanitize_app_name(name)) is None:
        return {"opened": False, "error": "application name contains characters that are not allowed"}
    try:
        if IS_WINDOWS:
            # start 找不到程序会弹框阻塞，不等待。
            subprocess.Popen(["cmd", "/c", "start", "", safe_name])
        elif IS_MACOS:
            result = subprocess.run(["open", "-a", safe_name], capture_output=True, text=True, timeout=15, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip() or f"open exited with code {result.returncode}"
                return {"opened": False, "error": detail}
        else:
            return {"opened": False, "error": "unsupported platform"}
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("open_application failed: %s", e)
        return {"opened": False, "error": str(e)}
    return {"opened": True, "name": safe_name}


def get_work_area() -> dict[str, int]:
    """主显示器扣除任务栏 / Dock 后的工作区 ``{x, y, w, h}``，与窗口快照同一坐标系。"""
    if IS_WINDOWS:
        return _work_area_windows()
    if IS_MACOS:
        return _work_area_macos()
    raise RuntimeError("unsupported platform")


def get_cursor_pos() -> dict[str, int]:
    """当前全局鼠标位置 ``{x, y}``。"""
    if IS_WINDOWS:
        return _cursor_windows()
    if IS_MACOS:
        return _cursor_macos()
    raise RuntimeError("unsupported platform")


def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    """在全局屏幕坐标 (x, y) 处模拟鼠标点击；button 为 left / right / middle。"""
    try:
        if IS_WINDOWS:
            _click_at_windows(x, y, button, clicks)
        elif IS_MACOS:
            _click_at_macos(x, y, button, clicks)
        else:
            return {"clicked": False, "error": "unsupported platform"}
    except Exception as e:
        logger.debug("click_at failed: %s", e)
        return {"clicked": False, "error": str(e)}
    return {"clicked": True, "x": x, "y": y, "button": button, "clicks": clicks}


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    return buf.value if _user32.GetClassNameW(hwnd, buf, 256) > 0 else ""


def _window_text(hwnd: int) -> str:
    if (length := _user32.GetWindowTextLengthW(hwnd)) <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _dwm_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """DWMWA_EXTENDED_FRAME_BOUNDS：物理像素的可见边界，不含透明缩放边框，也不受 DPI 虚拟化影响。"""
    rect = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    return (rect.left, rect.top, w, h) if w > 0 and h > 0 else None


def _process_exe(pid: int) -> str:
    """*pid* 的可执行文件名；无权限或进程已退出时返回空串。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        _kernel32.CloseHandle(handle)


def _work_area_windows() -> dict[str, int]:
    rect = wintypes.RECT()
    if not _user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        raise ctypes.WinError()
    return {"x": rect.left, "y": rect.top, "w": max(0, rect.right - rect.left), "h": max(0, rect.bottom - rect.top)}


def _work_area_macos() -> dict[str, int]:
    if not (screens := NSScreen.screens()):
        raise RuntimeError("no display found")
    primary = screens[0]
    full, visible = primary.frame(), primary.visibleFrame()
    # AppKit 原点在左下，换算成左上。
    top = full.size.height - (visible.origin.y + visible.size.height)
    return {"x": int(visible.origin.x), "y": int(top), "w": int(visible.size.width), "h": int(visible.size.height)}


def _cursor_windows() -> dict[str, int]:
    pt = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        raise ctypes.WinError()
    return {"x": pt.x, "y": pt.y}


def _cursor_macos() -> dict[str, int]:
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return {"x": int(loc.x), "y": int(loc.y)}


def _click_at_windows(x: int, y: int, button: str, clicks: int) -> None:
    down_flag, up_flag = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[button]
    if not _user32.SetCursorPos(x, y):
        raise ctypes.WinError()
    time.sleep(0.02)
    for _ in range(clicks):
        _user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.01)
        _user32.mouse_event(up_flag, 0, 0, 0, 0)
        time.sleep(0.02)


def _click_at_macos(x: int, y: int, button: str, clicks: int) -> None:
    down, up, mouse_button = {
        "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
        "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
        "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
    }[button]
    for click_state in range(1, clicks + 1):
        for event_type in (down, up):
            event = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), mouse_button)
            # 双击须递增 clickState。
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, click_state)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.01)
        time.sleep(0.02)


def _idle_windows() -> float:
    try:
        info = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return -1.0
        # dwTime 32 位毫秒，差值按模取。
        return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception as e:
        logger.debug("win idle probe failed: %s", e)
        return -1.0


def _idle_macos() -> float:
    try:
        secs = Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState,
            Quartz.kCGAnyInputEventType,
        )
    except Exception as e:
        logger.debug("macos idle probe failed: %s", e)
        return -1.0
    return max(0.0, float(secs))


def _locked_windows() -> bool:
    """锁屏时输入桌面切到 Winlogon，本会话取不到前台窗口；前台为锁屏窗口类时同样视为锁屏。"""
    try:
        if not (hwnd := _user32.GetForegroundWindow()):
            return True
        return _class_name(hwnd) in {"LockScreenBackstop", "LogonUI"}
    except Exception as e:
        logger.debug("win lock probe failed: %s", e)
        return False


def _locked_macos() -> bool:
    """锁屏时会话字典带 ``CGSSessionScreenIsLocked``；快速切换用户离开控制台时 ``kCGSSessionOnConsoleKey`` 为假。"""
    try:
        session = Quartz.CGSessionCopyCurrentDictionary()
    except Exception as e:
        logger.debug("macos lock probe failed: %s", e)
        return False
    if not session:
        return False
    return bool(session.get("CGSSessionScreenIsLocked", False)) or not bool(
        session.get("kCGSSessionOnConsoleKey", True),
    )


def _focus_windows() -> dict[str, Any]:
    try:
        if not (hwnd := _user32.GetForegroundWindow()):
            return {}
        # 前台是壳层时沿 Z 序找用户窗口。
        for _ in range(8):
            if _class_name(hwnd) not in _SHELL_WINDOW_CLASSES and _user32.IsWindowVisible(hwnd):
                break
            if not (next_hwnd := _user32.GetWindow(hwnd, _GW_HWNDNEXT)):
                break
            hwnd = next_hwnd

        # 取线程焦点窗口的顶层，避免停在壳层。
        info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
        _user32.GetGUIThreadInfo(_user32.GetWindowThreadProcessId(hwnd, None), ctypes.byref(info))
        real_hwnd = info.hwndFocus or info.hwndActive or hwnd
        top = _user32.GetAncestor(real_hwnd, _GA_ROOT) or real_hwnd

        pid = _window_pid(top)
        title = _window_text(top)
        result: dict[str, Any] = {
            "name": _process_exe(pid) or title,
            "pid": pid,
            "window_id": f"win:{top:X}",
            "title": title,
        }
        if bounds := _dwm_bounds(top):
            result |= dict(zip(("x", "y", "w", "h"), bounds, strict=True))
        return result
    except Exception as e:
        logger.debug("win focus probe failed: %s", e)
        return {}


def _focus_macos() -> dict[str, Any]:
    try:
        if not (app := NSWorkspace.sharedWorkspace().frontmostApplication()):
            return {}
        pid = app.processIdentifier()
        result: dict[str, Any] = {
            "name": app.localizedName() or "",
            "pid": pid,
            "bundle": app.bundleIdentifier() or "",
        }
        # 列表从前到后，取首个普通层窗口。
        for win in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
            if win.get("kCGWindowOwnerPID", -1) != pid or win.get("kCGWindowLayer", 0) != 0:
                continue
            b = win.get("kCGWindowBounds")
            if b and b.get("Width", 0) > 0 and b.get("Height", 0) > 0:
                result |= {
                    "window_id": f"mac:{int(win.get('kCGWindowNumber', 0))}",
                    "x": int(b.get("X", 0)),
                    "y": int(b.get("Y", 0)),
                    "w": int(b["Width"]),
                    "h": int(b["Height"]),
                }
                break
        return result
    except Exception as e:
        logger.debug("macos focus probe failed: %s", e)
        return {}


def _fullscreen_windows() -> bool:
    """前台窗口覆盖整个显示器（含任务栏区域）才算全屏；最大化窗口不覆盖任务栏，桌面外壳窗口不算。"""
    try:
        if not (hwnd := _user32.GetForegroundWindow()) or _class_name(hwnd) in _SHELL_WINDOW_CLASSES:
            return False
        win = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return False
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        monitor = _user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        mon = info.rcMonitor
        return win.left <= mon.left and win.top <= mon.top and win.right >= mon.right and win.bottom >= mon.bottom
    except Exception as e:
        logger.debug("win fullscreen probe failed: %s", e)
        return False


def _fullscreen_macos() -> bool:
    """前台应用最前面的普通窗口覆盖其所在显示器全部区域（原生全屏会隐藏菜单栏）时为 True。"""
    try:
        if not (app := NSWorkspace.sharedWorkspace().frontmostApplication()):
            return False
        pid = app.processIdentifier()
        bounds = next(
            (
                win["kCGWindowBounds"]
                for win in Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly,
                    Quartz.kCGNullWindowID,
                )
                if win.get("kCGWindowOwnerPID", -1) == pid and win.get("kCGWindowLayer", 0) == 0
            ),
            None,
        )
        if not bounds:
            return False
        x, y, w, h = bounds["X"], bounds["Y"], bounds["Width"], bounds["Height"]
        _, displays, _ = Quartz.CGGetActiveDisplayList(16, None, None)
        for display in displays or ():
            d = Quartz.CGDisplayBounds(display)
            dx, dy, dw, dh = d.origin.x, d.origin.y, d.size.width, d.size.height
            if dx <= x + w / 2 < dx + dw and dy <= y + h / 2 < dy + dh:
                return x <= dx and y <= dy and x + w >= dx + dw and y + h >= dy + dh
        return False
    except Exception as e:
        logger.debug("macos fullscreen probe failed: %s", e)
        return False


def _windows_windows() -> WindowScene:
    try:
        foreground = _user32.GetForegroundWindow()
        results: list[WindowInfo] = []
        exe_cache: dict[int, str] = {}

        def collect(hwnd: int, _lparam: int) -> bool:
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            cloaked = wintypes.DWORD()
            if (
                _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0
                and cloaked.value
            ):
                return True
            if _class_name(hwnd) in _SHELL_WINDOW_CLASSES or (bounds := _dwm_bounds(hwnd)) is None:
                return True
            if not (title := _window_text(hwnd)):
                return True
            pid = _window_pid(hwnd)
            if pid not in exe_cache:
                exe_cache[pid] = _process_exe(pid)
            if (exe := exe_cache[pid]).casefold() == "spiritagent.exe":
                return True
            x, y, w, h = bounds
            results.append(
                WindowInfo(
                    title=title,
                    name=exe or title,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    focused=hwnd == foreground,
                    visible=True,
                    window_id=f"win:{hwnd:X}",
                    pid=pid,
                    z_order=len(results),
                ),
            )
            return True

        if not _user32.EnumWindows(_WNDENUMPROC(collect), 0):
            raise ctypes.WinError()
        return WindowScene(windows=results, runner_instance_id=_WINDOW_SCENE_INSTANCE_ID)
    except Exception as e:
        logger.debug("win get_windows failed: %s", e)
        return WindowScene()


def _windows_macos() -> WindowScene:
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        focused_pid = app.processIdentifier() if app else 0
        results: list[WindowInfo] = []
        focused_window_seen = False
        for win in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
            if win.get("kCGWindowLayer", 0) != 0:
                continue
            b = win.get("kCGWindowBounds")
            if not b or b.get("Width", 0) <= 0 or b.get("Height", 0) <= 0:
                continue
            owner = win.get("kCGWindowOwnerName", "") or ""
            if owner.casefold() in {"spiritagent", "唤生"}:
                continue
            # 前台应用首个窗口即焦点。
            focused = win.get("kCGWindowOwnerPID", -1) == focused_pid and not focused_window_seen
            focused_window_seen |= focused
            results.append(
                WindowInfo(
                    title=win.get("kCGWindowName", "") or owner,
                    name=owner,
                    x=int(b.get("X", 0)),
                    y=int(b.get("Y", 0)),
                    w=int(b["Width"]),
                    h=int(b["Height"]),
                    focused=focused,
                    visible=True,
                    window_id=f"mac:{int(win.get('kCGWindowNumber', 0))}",
                    pid=int(win.get("kCGWindowOwnerPID", 0)),
                    z_order=len(results),
                ),
            )
        return WindowScene(windows=results, runner_instance_id=_WINDOW_SCENE_INSTANCE_ID)
    except Exception as e:
        logger.debug("macos get_windows failed: %s", e)
        return WindowScene()
