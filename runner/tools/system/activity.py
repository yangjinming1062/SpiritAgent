import logging
import re
import subprocess
import time
from typing import Any

from utils import IS_MACOS, IS_WINDOWS

logger = logging.getLogger(__name__)
try:
    import psutil  # type: ignore[import-not-found]
except ImportError:
    psutil = None  # type: ignore[assignment]
try:
    import ctypes
    from ctypes import wintypes
except ImportError:
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
try:
    from Quartz import (  # type: ignore[import-not-found]
        CGEventSourceSecondsSinceLastEventType,
        CGWindowListCopyWindowInfo,
        kCGAnyInputEventType,
        kCGEventSourceStateHIDSystemState,
        kCGNullWindowId,
        kCGWindowListOptionOnScreenOnly,
    )
except ImportError:
    CGEventSourceSecondsSinceLastEventType = None  # type: ignore[assignment,misc]
    CGWindowListCopyWindowInfo = None  # type: ignore[assignment,misc]
    kCGAnyInputEventType = None  # type: ignore[assignment,misc]
    kCGEventSourceStateHIDSystemState = None  # type: ignore[assignment,misc]
    kCGNullWindowId = None  # type: ignore[assignment,misc]
    kCGWindowListOptionOnScreenOnly = None  # type: ignore[assignment,misc]
try:
    import Quartz  # type: ignore[import-not-found]
except ImportError:
    Quartz = None  # type: ignore[assignment]
try:
    from AppKit import (
        NSScreen,  # type: ignore[import-not-found]
        NSWorkspace,  # type: ignore[import-not-found]
    )
except ImportError:
    NSScreen = None  # type: ignore[assignment,misc]
    NSWorkspace = None  # type: ignore[assignment]


def get_idle_seconds() -> float:
    """自上次用户输入以来的秒数; 不可用时返回 ``-1.0``。"""
    if IS_WINDOWS:
        return _idle_windows()
    if IS_MACOS:
        return _idle_macos()
    return -1.0


def is_screen_locked() -> bool:
    """当且仅当工作站会话已锁屏; 无法判断时返回 ``False``(误报"已锁"比漏报更糟)。"""
    if IS_WINDOWS:
        return _locked_windows()
    if IS_MACOS:
        return _locked_macos()
    return False


def get_focused_app() -> dict[str, Any]:
    """前台应用的 ``{name, pid, kind}``; 无法判断时返回 ``{}``。"""
    if IS_WINDOWS:
        return _focus_windows()
    if IS_MACOS:
        return _focus_macos()
    return {}


def is_fullscreen() -> bool:
    """当且仅当前台窗口覆盖其显示器工作区 ≥ 95%; 未知/不可用时返回 ``False``。"""
    if IS_WINDOWS:
        return _fullscreen_windows()
    if IS_MACOS:
        return _fullscreen_macos()
    return False


def get_power_state() -> dict[str, Any]:
    """``{on_battery, screen_on, charging}`` — 布尔值默认 ``False``/``True``。"""
    state: dict[str, Any] = {"on_battery": False, "screen_on": True, "charging": False}
    if psutil is not None:
        try:
            battery = psutil.sensors_battery()
        except Exception as e:
            logger.debug("psutil.sensors_battery failed: %s", e)
        else:
            if battery is not None:
                state["on_battery"] = not battery.power_plugged
                state["charging"] = battery.power_plugged and battery.percent < 100
    return state


def get_windows() -> dict[str, Any]:
    """可见顶层窗口列表 ``{"windows": [{title, name, x, y, w, h, focused}, ...]}``; 不可用时为空列表。"""
    if IS_WINDOWS:
        return _windows_windows()
    if IS_MACOS:
        return _windows_macos()
    return {"windows": []}


_APP_NAME_FORBIDDEN_CHARS = frozenset("&|<>^\"'$()\\;{}\n\r\t")
_APP_NAME_FORBIDDEN_RE = re.compile(r"\.\.|/ (?= )")


def _sanitize_app_name(name: str) -> str | None:
    """拒绝含 cmd.exe / shell 元字符的应用名, 防 ``start`` / ``open -a`` 被拼接注入。"""
    if not name:
        return None
    stripped = name.strip()
    if not stripped:
        return None
    if any(ch in _APP_NAME_FORBIDDEN_CHARS for ch in stripped):
        return None
    if _APP_NAME_FORBIDDEN_RE.search(stripped):
        return None
    return stripped


def open_application(name: str) -> dict[str, Any]:
    """启动 *name*(可执行名 / app 名 / 路径), 返回 ``{opened, name}``。"""
    if not name or not str(name).strip():
        return {"opened": False, "error": "application name is required"}
    safe_name = _sanitize_app_name(name)
    if safe_name is None:
        return {"opened": False, "error": "application name contains forbidden shell metacharacters"}
    try:
        if IS_WINDOWS:
            subprocess.Popen(["cmd", "/c", "start", "", safe_name])
        elif IS_MACOS:
            subprocess.Popen(["open", "-a", safe_name])
        return {"opened": True, "name": safe_name}
    except Exception as e:
        logger.debug("open_application failed: %s", e)
        return {"opened": False, "error": str(e)}


def get_work_area() -> dict[str, Any]:
    """主显示器工作区的 ``{x, y, w, h}``(已扣除任务栏/dock)。"""
    if IS_WINDOWS:
        return _work_area_windows()
    if IS_MACOS:
        return _work_area_macos()
    return {"x": 0, "y": 0, "w": 1920, "h": 1080}


def get_cursor_pos() -> dict[str, Any]:
    """当前全局鼠标位置的 ``{x, y}``。"""
    if IS_WINDOWS:
        return _cursor_windows()
    if IS_MACOS:
        return _cursor_macos()
    return {"x": 0, "y": 0}


def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    """在全局屏幕坐标 (x, y) 处模拟一次鼠标点击。"""
    if IS_WINDOWS:
        return _click_at_windows(x, y, button, clicks)
    if IS_MACOS:
        return _click_at_macos(x, y, button, clicks)
    return {"clicked": False, "error": "unsupported platform"}


def _work_area_windows() -> dict[str, int]:
    if ctypes is None or wintypes is None:
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}
    try:
        user32 = ctypes.windll.user32

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        rect = _RECT()
        # Use SPI_GETWORKAREA (0x0030) to get the work area rectangle
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return {
                "x": int(rect.left),
                "y": int(rect.top),
                "w": int(max(0, rect.right - rect.left)),
                "h": int(max(0, rect.bottom - rect.top)),
            }
    except Exception as e:
        logger.debug("win work_area probe failed: %s", e)
    return {"x": 0, "y": 0, "w": 1920, "h": 1080}


def _work_area_macos() -> dict[str, int]:
    if NSWorkspace is None:
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}
    try:
        screen = NSScreen.mainScreen()
        if screen:
            frame = screen.visibleFrame()
            return {
                "x": int(frame.origin.x),
                "y": int(frame.origin.y),
                "w": int(frame.size.width),
                "h": int(frame.size.height),
            }
    except Exception as e:
        logger.debug("macos work_area probe failed: %s", e)
    return {"x": 0, "y": 0, "w": 1920, "h": 1080}


def _cursor_windows() -> dict[str, int]:
    if ctypes is None or wintypes is None:
        return {"x": 0, "y": 0}
    try:
        user32 = ctypes.windll.user32
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            return {"x": int(pt.x), "y": int(pt.y)}
    except Exception as e:
        logger.debug("win cursor probe failed: %s", e)
    return {"x": 0, "y": 0}


def _cursor_macos() -> dict[str, int]:
    if Quartz is None:
        return {"x": 0, "y": 0}
    try:
        event = Quartz.CGEventCreate(None)
        if event:
            loc = Quartz.CGEventGetLocation(event)
            return {"x": int(loc.x), "y": int(loc.y)}
    except Exception as e:
        logger.debug("macos cursor probe failed: %s", e)
    return {"x": 0, "y": 0}


def _click_at_windows(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    if ctypes is None:
        return {"clicked": False, "error": "ctypes unavailable"}
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(x, y)
        time.sleep(0.02)
        btn_lower = button.lower()
        if btn_lower == "right":
            down_flag, up_flag = 0x0008, 0x0010
        elif btn_lower == "middle":
            down_flag, up_flag = 0x0020, 0x0040
        else:
            down_flag, up_flag = 0x0002, 0x0004

        for _ in range(max(1, clicks)):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.01)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            time.sleep(0.02)
        return {"clicked": True, "x": x, "y": y, "button": button, "clicks": clicks}
    except Exception as e:
        logger.debug("win click_at failed: %s", e)
        return {"clicked": False, "error": str(e)}


def _click_at_macos(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    if Quartz is None:
        return {"clicked": False, "error": "Quartz unavailable"}
    try:
        btn_lower = button.lower()
        if btn_lower == "right":
            down_evt = Quartz.kCGEventRightMouseDown
            up_evt = Quartz.kCGEventRightMouseUp
            btn_type = Quartz.kCGMouseButtonRight
        else:
            down_evt = Quartz.kCGEventLeftMouseDown
            up_evt = Quartz.kCGEventLeftMouseUp
            btn_type = Quartz.kCGMouseButtonLeft

        for _ in range(max(1, clicks)):
            event_down = Quartz.CGEventCreateMouseEvent(None, down_evt, (x, y), btn_type)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)
            time.sleep(0.01)
            event_up = Quartz.CGEventCreateMouseEvent(None, up_evt, (x, y), btn_type)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)
            time.sleep(0.02)
        return {"clicked": True, "x": x, "y": y, "button": button, "clicks": clicks}
    except Exception as e:
        logger.debug("macos click_at failed: %s", e)
        return {"clicked": False, "error": str(e)}


def _idle_windows() -> float:
    if ctypes is None or wintypes is None:
        return -1.0
    try:

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            ticks = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, (ticks - info.dwTime) / 1000.0)
    except Exception as e:
        logger.debug("win idle probe failed: %s", e)
    return -1.0


def _idle_macos() -> float:
    if CGEventSourceSecondsSinceLastEventType is None:
        return -1.0
    secs = CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateHIDSystemState, kCGAnyInputEventType)
    return float(max(0.0, secs))


def _locked_windows() -> bool:
    """检测 Windows 桌面是否已锁屏。

    组合三个互相独立的信号(任一为真 ⇒ 锁屏):
      1. ``GetForegroundWindow() == NULL`` — 桌面自身没有前台窗口。
      2. ``GetClassName(hwnd) == 'LockScreenBackstop' / 'LogonUI'`` — Win10/11 锁屏窗口类。
      3. 前台线程输入桌面的 ``GetUserObjectInformation()`` 返回非默认桌面名。
    """
    if ctypes is None:
        return False
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return True
        # (2) 类名匹配 — LogonUI 即 Win10/11 的锁屏。
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value if n > 0 else ""
        if cls in ("LockScreenBackstop", "LogonUI"):
            return True
        # (3) 输入桌面切换 — ``GetUserObjectInformation`` 是规范 API, 但成本高, 只在前两个便宜信号未触发时调用。
        try:
            thread_id = user32.GetWindowThreadProcessId(hwnd, None)
            input_desktop = user32.GetThreadDesktop(thread_id)
            default_desktop = user32.GetThreadDesktop(0)
            if input_desktop and default_desktop and input_desktop != default_desktop:
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.debug("win lock probe failed: %s", e)
        return False


def _locked_macos() -> bool:
    """当且仅当当前 macOS 会话已锁屏。

    新版 ``kCGSSessionOnConsoleKey`` 在控制台上有用户登录(正常用 + 锁屏)时都返回 ``1``;
    旧版驼峰命名 ``CGSSessionOnConsoleKey`` 只在控制台无用户(锁屏/loginwindow)时出现 — 因此:
    锁屏 ⇔ 旧 key 存在 **或** 新 key 不存在。
    """
    if Quartz is None:
        return False
    try:
        d = Quartz.CGSessionCopyCurrentDictionary()
    except Exception as e:
        logger.debug("macos lock probe failed: %s", e)
        return False
    if not d:
        return False
    on_console = bool(d.get("kCGSSessionOnConsoleKey", 0))
    legacy_locked = "CGSSessionOnConsoleKey" in d and d.get("CGSSessionOnConsoleKey") is not None
    return legacy_locked or not on_console


def _focus_windows() -> dict[str, Any]:
    """检查 Windows 前台窗口(遍历 explorer 容器并读真正焦点 hwnd)。"""
    if ctypes is None or wintypes is None:
        return {}
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        # 跳过 explorer 容器与不可见系统窗口 — 向后遍历 Z-order 找到用户实际交互的顶层可见窗口。
        for _ in range(8):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value not in ("Shell_TrayWnd", "WorkerW", "Progman") and user32.IsWindowVisible(hwnd):
                break
            next_hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT = 2
            if not next_hwnd:
                break
            hwnd = next_hwnd

        # GetGUIThreadInfo: 读前台线程上真正 focus 的 hwnd(用户实际输入窗口, 而非最顶层 shell 容器)。
        class _GuiThreadInfo(ctypes.Structure):
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

        tid = user32.GetWindowThreadProcessId(hwnd, None)
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        user32.GetGUIThreadInfo(tid, ctypes.byref(info))
        real_hwnd = info.hwndFocus or info.hwndActive or hwnd
        # 取焦点窗口的顶层 owner (GA_ROOT = 2)，若返回 0 则兜底回退到 real_hwnd / hwnd。
        top = user32.GetAncestor(real_hwnd, 2) or real_hwnd or hwnd

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(top, ctypes.byref(pid))
        title_buf = ctypes.create_unicode_buffer(512)
        length = user32.GetWindowTextW(top, title_buf, 512)
        title = title_buf.value[:length]
        exe = _process_exe(pid.value)
        rect = wintypes.RECT()
        user32.GetWindowRect(top, ctypes.byref(rect))
        return {
            "name": exe or title,
            "pid": pid.value,
            "title": title,
            "kind": "user",
            "x": rect.left,
            "y": rect.top,
            "w": max(0, rect.right - rect.left),
            "h": max(0, rect.bottom - rect.top),
        }
    except Exception as e:
        logger.debug("win focus probe failed: %s", e)
        return {}


def _focus_macos() -> dict[str, Any]:
    if NSWorkspace is None:
        return {}
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return {}
        result: dict[str, Any] = {
            "name": app.localizedName() or "",
            "pid": app.processIdentifier(),
            "bundle": app.bundleIdentifier() or "",
            "kind": "user",
        }
        if CGWindowListCopyWindowInfo is not None:
            for win in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId):
                if win.get("kCGWindowOwnerPID", -1) != app.processIdentifier():
                    continue
                b = win.get("kCGWindowBounds")
                if b and b.get("Width", 0) > 0:
                    result["x"] = int(b.get("X", 0))
                    result["y"] = int(b.get("Y", 0))
                    result["w"] = int(b["Width"])
                    result["h"] = int(b["Height"])
                    break
        return result
    except Exception as e:
        logger.debug("macos focus probe failed: %s", e)
        return {}


_FULLSCREEN_COVERAGE_RATIO = 0.95


def _fullscreen_windows() -> bool:
    """前台窗口覆盖其显示器工作区 ≥ 95%。"""
    if ctypes is None or wintypes is None:
        return False
    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        class _Rect(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        win = _Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return False
        win_w = max(0, win.right - win.left)
        win_h = max(0, win.bottom - win.top)
        if win_w <= 0 or win_h <= 0:
            return False

        monitor = user32.MonitorFromWindow(hwnd, 0x00000002)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False
        monitor_info = type(
            "MI",
            (ctypes.Structure,),
            {
                "_fields_": [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", _Rect),
                    ("rcWork", _Rect),
                    ("dwFlags", wintypes.DWORD),
                ],
            },
        )()
        monitor_info.cbSize = ctypes.sizeof(monitor_info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return False
        # 跟 *完整* 显示器区域 ``rcMonitor`` 比, 而不是工作区 ``rcWork``: 工作区不含任务栏,
        # 已最大化窗口就一定覆盖工作区, 绝大多数用户的正常工作态会被误判成全屏; ``rcMonitor``
        # 包含任务栏区, 只有真正进入全屏(任务栏自动隐藏)才能达到阈值。
        monitor = monitor_info.rcMonitor
        monitor_w = max(1, monitor.right - monitor.left)
        monitor_h = max(1, monitor.bottom - monitor.top)
        ratio = min(win_w / monitor_w, win_h / monitor_h)
        return ratio >= _FULLSCREEN_COVERAGE_RATIO
    except Exception as e:
        logger.debug("win fullscreen probe failed: %s", e)
        return False


def _fullscreen_macos() -> bool:
    """当且仅当前台 app 的 key window 在原生全屏模式(绿色按钮触发的那种)。

    ``NSApplication.sharedApplication().windows()`` 只枚举 *Runner 自己进程内* 的 NSWindow(Runner 是无头 Python 进程,
    根本没有); 跨进程窗口枚举必须走 CoreGraphics 的 ``CGWindowListCopyWindowInfo``, 这里就用它配合 ``NSWorkspace``
    拿到的前台 PID 来完成。
    """
    if NSWorkspace is None or Quartz is None or CGWindowListCopyWindowInfo is None:
        return False
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if not app:
            return False
        focused_pid = app.processIdentifier()
        # kCGWindowListOptionOnScreenOnly 排除离屏/最小化窗口; 再按 PID 过滤, 看 AppKit 头文件暴露的全屏位/窗口状态位。
        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId)
        NSWindowStyleMaskFullScreen = 1 << 14  # 来自 AppKit 头文件
        for win in windows:
            owner_pid = win.get("kCGWindowOwnerPID", -1)
            if owner_pid != focused_pid:
                continue
            # 窗口状态位 1 << 9 是现代 macOS 的 ``kCGWindowStateIsInFullscreen`` — 老版本的头文件没暴露这个常量, 直接按位掩码兜底。
            if win.get("kCGWindowIsInFullscreen", 0) & 1:
                return True
            style_mask = win.get("kCGWindowStyleMask", 0)
            if style_mask & NSWindowStyleMaskFullScreen:
                return True
        return False
    except Exception as e:
        logger.debug("macos fullscreen probe failed: %s", e)
        return False


_SHELL_WINDOW_CLASSES = frozenset(("Shell_TrayWnd", "WorkerW", "Progman"))


def _process_exe(pid: int) -> str:
    """尽力获取 *pid* 的可执行文件名; 失败时返回空串。"""
    if ctypes is None or wintypes is None:
        return ""
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        h = kernel32.OpenProcess(0x1000, False, pid)
        try:
            buf = ctypes.create_unicode_buffer(512)
            psapi.GetModuleFileNameExW(h, None, buf, 512)
            return buf.value.rsplit("\\", 1)[-1] if buf.value else ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def _windows_windows() -> dict[str, Any]:
    if ctypes is None or wintypes is None:
        return {"windows": []}
    try:
        user32 = ctypes.windll.user32
        foreground = user32.GetForegroundWindow()
        results: list[dict[str, Any]] = []
        exe_cache: dict[int, str] = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in _SHELL_WINDOW_CLASSES:
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = max(0, rect.right - rect.left)
            h = max(0, rect.bottom - rect.top)
            if w <= 0 or h <= 0:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            tb = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, tb, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value
            exe = exe_cache.get(pid_val) or exe_cache.setdefault(pid_val, _process_exe(pid_val))
            results.append(
                {
                    "title": tb.value,
                    "name": exe or tb.value,
                    "x": rect.left,
                    "y": rect.top,
                    "w": w,
                    "h": h,
                    "focused": hwnd == foreground,
                },
            )
            return True

        user32.EnumWindows(cb, 0)
        return {"windows": results}
    except Exception as e:
        logger.debug("win get_windows failed: %s", e)
        return {"windows": []}


def _windows_macos() -> dict[str, Any]:
    if Quartz is None or CGWindowListCopyWindowInfo is None:
        return {"windows": []}
    try:
        focused_pid = 0
        if NSWorkspace is not None:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app:
                focused_pid = app.processIdentifier()
        results: list[dict[str, Any]] = []
        for win in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowId):
            if win.get("kCGWindowLayer", 0) != 0:
                continue
            b = win.get("kCGWindowBounds")
            if not b or b.get("Width", 0) <= 0:
                continue
            owner = win.get("kCGWindowOwnerName", "")
            results.append(
                {
                    "title": win.get("kCGWindowName", "") or owner,
                    "name": owner,
                    "x": int(b.get("X", 0)),
                    "y": int(b.get("Y", 0)),
                    "w": int(b["Width"]),
                    "h": int(b["Height"]),
                    "focused": win.get("kCGWindowOwnerPID", -1) == focused_pid,
                },
            )
        return {"windows": results}
    except Exception as e:
        logger.debug("macos get_windows failed: %s", e)
        return {"windows": []}
