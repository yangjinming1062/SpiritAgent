import base64
import contextlib
import ctypes.wintypes
import functools
import io
import logging
import re
import sys
import threading
from typing import Any

try:
    import mss
except ImportError:
    mss = None  # type: ignore[assignment]
import psutil

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore[assignment]
import pyperclip

try:
    import pywinauto
except ImportError:
    pywinauto = None  # type: ignore[assignment]
from PIL import Image, ImageDraw, ImageFont

from .cu_backend import DESKTOP_SENTINELS, ActionResult, CaptureResult, ComputerUseBackend, UIElement

logger = logging.getLogger(__name__)

# COM/UIA 接口指针与 apartment 亲和，pyautogui 同一时刻只能驱动一套物理鼠标/键盘 — 桌面自动化不能并发执行
_serial_lock = threading.RLock()


def _serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _serial_lock:
            return fn(*args, **kwargs)

    return wrapper


_WINDOWS_KEY_MAP = {
    "cmd": "win",
    "command": "win",
    "meta": "win",
    "option": "alt",
    "return": "enter",
    "delete": "delete",
    "escape": "escape",
    "backspace": "backspace",
    "space": "space",
    "tab": "tab",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
}

PW_RENDERFULLCONTENT = 0x00000002

# app= 的哨兵值集中定义在 cu_backend.DESKTOP_SENTINELS，防止 macOS 与 Windows 后端产生分歧
# Windows 类名由 GetClassNameW 按规范大小写原样返回；忽略大小写匹配，使 OS 给出的 'Progman' / 'Shell_TrayWnd' 与小写拼写落到同一集合项
_WIN_SHELL_CLASSES = frozenset({"progman", "shell_traywnd"})


def _is_win32() -> bool:
    return sys.platform == "win32"


def _map_key(key: str) -> str:
    return _WINDOWS_KEY_MAP.get(key.lower(), key.lower())


def _get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def _get_dpi_scale(hwnd: int) -> float:
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(hwnd)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def _enum_windows_for_pid(pid: int) -> list[int]:
    result = []
    EnumWindows = ctypes.windll.user32.EnumWindows
    GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible

    def callback(hwnd, _):
        proc_id = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    EnumWindows(WNDENUMPROC(callback), 0)
    return result


def _capture_screen_region(x: int, y: int, w: int, h: int) -> bytes:
    with mss.MSS() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        img = sct.grab(monitor)
        return mss.tools.to_png(img.rgb, img.size)


def _capture_window_printwindow(hwnd: int) -> bytes | None:
    try:
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None

        hdc_window = ctypes.windll.user32.GetDC(hwnd)
        hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_window)
        hbitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_window, width, height)
        try:
            ctypes.windll.gdi32.SelectObject(hdc_mem, hbitmap)
            png_bytes = None
            if ctypes.windll.user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT):
                png_bytes = _bitmap_to_png(hdc_mem, hbitmap, width, height)
        finally:
            # 上面获取的每个 GDI 句柄都必须在像素拷贝中途抛错时也释放 — 泄漏会按 capture 调用累积
            ctypes.windll.gdi32.DeleteObject(hbitmap)
            ctypes.windll.gdi32.DeleteDC(hdc_mem)
            ctypes.windll.user32.ReleaseDC(hwnd, hdc_window)
        return png_bytes
    except Exception as e:
        logger.debug("PrintWindow capture failed: %s", e)
        return None


def _bitmap_to_png(hdc_mem: int, hbitmap: int, width: int, height: int) -> bytes:
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.wintypes.DWORD),
            ("biWidth", ctypes.wintypes.LONG),
            ("biHeight", ctypes.wintypes.LONG),
            ("biPlanes", ctypes.wintypes.WORD),
            ("biBitCount", ctypes.wintypes.WORD),
            ("biCompression", ctypes.wintypes.DWORD),
            ("biSizeImage", ctypes.wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.wintypes.LONG),
            ("biYPelsPerMeter", ctypes.wintypes.LONG),
            ("biClrUsed", ctypes.wintypes.DWORD),
            ("biClrImportant", ctypes.wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf_size = width * height * 4
    buf = ctypes.create_string_buffer(buf_size)
    ctypes.windll.gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
    png_io = io.BytesIO()
    img.save(png_io, format="PNG")
    return png_io.getvalue()


def _draw_som_overlay(png_bytes: bytes, elements: list[UIElement]) -> tuple[str, int, int]:
    img = Image.open(io.BytesIO(png_bytes))
    draw = ImageDraw.Draw(img)
    width, height = img.size

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    for elem in elements:
        x, y, w, h = elem.bounds
        if w <= 0 or h <= 0:
            continue
        draw.rectangle([x, y, x + w, y + h], outline="red", width=2)
        label = str(elem.index)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rectangle([x, y, x + tw + 4, y + th + 4], fill="red")
        draw.text((x + 2, y + 2), label, fill="white", font=font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode(), width, height


class WinBackend(ComputerUseBackend):
    def __init__(self) -> None:
        self._active_hwnd: int | None = None
        self._active_app: str = ""
        self._last_app: str | None = None
        self._element_cache: list[UIElement] = []
        self._dpi_scale: float = 1.0
        self._desktop = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self._active_hwnd = None
        self._element_cache = []
        self._desktop = None

    def is_available(self) -> bool:
        if not _is_win32():
            return False
        return pywinauto is not None and mss is not None and pyautogui is not None

    @_serialized
    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        if app and app.lower() in DESKTOP_SENTINELS:
            hwnd, app_name = self._find_shell_window()
            if hwnd is None:
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no shell window found; sentinel app={app!r} requires Progman or Shell_TrayWnd to be enumerable>",
                )
            self._active_hwnd = hwnd
            self._active_app = app_name
            self._last_app = app_name
        elif app:
            hwnd, app_name = self._find_window_by_app(app)
            if hwnd is None:
                return CaptureResult(
                    mode=mode,
                    width=0,
                    height=0,
                    window_title=f"<no window matched app={app!r}; call list_apps to see available apps>",
                )
            self._active_hwnd = hwnd
            self._active_app = app_name
            self._last_app = app_name
        elif self._active_hwnd is None:
            self._active_hwnd = ctypes.windll.user32.GetForegroundWindow()
            self._active_app = self._get_hwnd_title(self._active_hwnd)

        self._dpi_scale = _get_dpi_scale(self._active_hwnd)
        elements = []

        if mode in {"som", "ax"}:
            elements = self._enumerate_uia_elements()
            self._element_cache = elements

        png_b64 = None
        width, height = 0, 0
        png_bytes_len = 0

        if mode in {"som", "vision"}:
            png_bytes = self._capture_active_window()
            if png_bytes:
                if mode == "som" and elements:
                    png_b64, width, height = _draw_som_overlay(png_bytes, elements)
                    png_bytes = base64.b64decode(png_b64)
                else:
                    png_b64 = base64.b64encode(png_bytes).decode()
                    img = Image.open(io.BytesIO(png_bytes))
                    width, height = img.size
                png_bytes_len = len(png_bytes)

        window_title = self._get_hwnd_title(self._active_hwnd) if self._active_hwnd else ""

        return CaptureResult(
            mode=mode,
            width=width,
            height=height,
            png_b64=png_b64,
            elements=elements,
            app=self._active_app,
            window_title=window_title,
            png_bytes_len=png_bytes_len,
            dpi_scale=self._dpi_scale,
        )

    @_serialized
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
        if element is not None:
            if element < 0 or element >= len(self._element_cache):
                return ActionResult(
                    ok=False,
                    action="click",
                    message=f"element {element} out of range (0-{len(self._element_cache) - 1})",
                )
            x, y = self._element_cache[element].center()
        elif x is not None and y is not None:
            pass
        else:
            return ActionResult(ok=False, action="click", message="click requires element= or coordinate=")

        try:
            pyautogui.moveTo(x, y, _pause=False)
            for mod in modifiers or []:
                pyautogui.keyDown(_map_key(mod))
            pyautogui.click(x, y, clicks=click_count, button=button)
            for mod in modifiers or []:
                pyautogui.keyUp(_map_key(mod))
            return ActionResult(ok=True, action="click", message=f"clicked at ({x}, {y})")
        except Exception as e:
            return ActionResult(ok=False, action="click", message=str(e))

    @_serialized
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
        start = self._resolve_point(from_element, from_xy)
        end = self._resolve_point(to_element, to_xy)
        if start is None or end is None:
            return ActionResult(ok=False, action="drag", message="drag requires from/to element or coordinate")

        sx, sy = start
        ex, ey = end

        try:
            pyautogui.moveTo(sx, sy, _pause=False)
            for mod in modifiers or []:
                pyautogui.keyDown(_map_key(mod))
            pyautogui.drag(ex - sx, ey - sy, duration=0.5, button=button)
            for mod in modifiers or []:
                pyautogui.keyUp(_map_key(mod))
            return ActionResult(ok=True, action="drag", message=f"dragged ({sx},{sy}) -> ({ex},{ey})")
        except Exception as e:
            return ActionResult(ok=False, action="drag", message=str(e))

    @_serialized
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
        if element is not None:
            if element < 0 or element >= len(self._element_cache):
                return ActionResult(ok=False, action="scroll", message=f"element {element} out of range")
            x, y = self._element_cache[element].center()
        elif x is not None and y is not None:
            pass
        else:
            x, y = pyautogui.position()

        ticks = max(1, min(50, amount))
        try:
            for mod in modifiers or []:
                pyautogui.keyDown(_map_key(mod))
            if direction in ("up", "down"):
                pyautogui.scroll(ticks if direction == "up" else -ticks, x, y)
            elif direction in ("left", "right"):
                pyautogui.hscroll(ticks if direction == "right" else -ticks, x, y)
            for mod in modifiers or []:
                pyautogui.keyUp(_map_key(mod))
            return ActionResult(ok=True, action="scroll", message=f"scrolled {direction} x{ticks} at ({x},{y})")
        except Exception as e:
            return ActionResult(ok=False, action="scroll", message=str(e))

    @_serialized
    def type_text(self, text: str) -> ActionResult:
        try:
            if all(ord(c) < 128 for c in text):
                pyautogui.write(text, interval=0.02)
            else:
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            return ActionResult(ok=True, action="type_text", message=f"typed {len(text)} chars")
        except Exception as e:
            return ActionResult(ok=False, action="type_text", message=str(e))

    @_serialized
    def key(self, keys: str) -> ActionResult:
        parts = [p.strip().lower() for p in re.split(r"[+\-]", keys) if p.strip()]
        if not parts:
            return ActionResult(ok=False, action="key", message=f"empty key combo: {keys!r}")

        mapped = [_map_key(p) for p in parts]
        try:
            if len(mapped) > 1:
                pyautogui.hotkey(*mapped)
            else:
                pyautogui.press(mapped[0])
            return ActionResult(ok=True, action="key", message=f"pressed {keys!r}")
        except Exception as e:
            return ActionResult(ok=False, action="key", message=str(e))

    @_serialized
    def list_apps(self) -> list[dict[str, Any]]:
        seen = set()
        apps = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info["pid"]
                if pid in seen:
                    continue
                hwnds = _enum_windows_for_pid(pid)
                if hwnds:
                    seen.add(pid)
                    apps.append({"name": proc.info["name"], "pid": pid, "window_count": len(hwnds)})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return apps

    @_serialized
    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        hwnd, app_name = self._find_window_by_app(app)
        if hwnd is None:
            return ActionResult(ok=False, action="focus_app", message=f"No window found for app '{app}'")

        self._active_hwnd = hwnd
        self._active_app = app_name
        self._last_app = app_name

        if raise_window:
            try:
                win = self._get_desktop().window(handle=hwnd)
                win.set_focus()
                return ActionResult(ok=True, action="focus_app", message=f"Focused and raised {app_name} (hwnd={hwnd})")
            except Exception as e:
                return ActionResult(ok=False, action="focus_app", message=f"Failed to raise window: {e}")

        return ActionResult(ok=True, action="focus_app", message=f"Targeted {app_name} (hwnd={hwnd}) without raising")

    @_serialized
    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        if element is None:
            return ActionResult(ok=False, action="set_value", message="set_value requires element=")
        if element < 0 or element >= len(self._element_cache):
            return ActionResult(ok=False, action="set_value", message=f"element {element} out of range")

        try:
            win = self._get_desktop().window(handle=self._active_hwnd)
            descendants = win.descendants()
            ui_elem = self._element_cache[element]
            target = None
            for ctrl in descendants:
                rect = ctrl.rectangle()
                if (rect.left, rect.top, rect.width(), rect.height()) == ui_elem.bounds:
                    target = ctrl
                    break
            if target is None:
                return ActionResult(ok=False, action="set_value", message=f"Could not find UI element #{element}")

            ctrl_type = ui_elem.role.lower()
            if "combo" in ctrl_type or "list" in ctrl_type:
                target.select(value)
            elif "slider" in ctrl_type or "scroll" in ctrl_type:
                target.set_value(value)
            else:
                target.set_edit_text(value)
            return ActionResult(ok=True, action="set_value", message=f"Set value on element #{element}")
        except Exception as e:
            return ActionResult(ok=False, action="set_value", message=str(e))

    def _get_desktop(self) -> Any:
        if self._desktop is None:
            self._desktop = pywinauto.Desktop(backend="uia")
        return self._desktop

    def _find_window_by_app(self, app: str) -> tuple[int | None, str]:
        app_lower = app.lower()
        desktop = self._get_desktop()
        for win in desktop.windows():
            try:
                title = win.window_text()
                if app_lower in title.lower():
                    return win.handle, title
            except Exception:
                continue

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if app_lower in (proc.info["name"] or "").lower():
                    hwnds = _enum_windows_for_pid(proc.info["pid"])
                    if hwnds:
                        return hwnds[0], proc.info["name"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        return None, ""

    def _find_shell_window(self) -> tuple[int | None, str]:
        """返回最顶层的 Progman / Shell_TrayWnd 窗口，供 app='desktop' 哨兵使用。"""
        try:
            desktop = self._get_desktop()
            for win in desktop.windows():
                cls = _get_class_name(win.handle)
                if cls.lower() in _WIN_SHELL_CLASSES:
                    return win.handle, cls
        except Exception:
            pass
        EnumWindows = ctypes.windll.user32.EnumWindows
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        result: list[tuple[int, str]] = []

        def callback(hwnd, _):
            if not IsWindowVisible(hwnd):
                return True
            cls = _get_class_name(hwnd)
            if cls.lower() in _WIN_SHELL_CLASSES:
                result.append((hwnd, cls))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        EnumWindows(WNDENUMPROC(callback), 0)
        if result:
            return result[0]
        return None, ""

    def _get_hwnd_title(self, hwnd: int) -> str:
        try:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value
        except Exception:
            pass
        return ""

    def _capture_active_window(self) -> bytes | None:
        if not self._active_hwnd:
            return None

        try:
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(self._active_hwnd, ctypes.byref(rect))
            x, y = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 0 and h > 0:
                return _capture_screen_region(x, y, w, h)
        except Exception as e:
            logger.debug("mss capture failed: %s", e)

        pw_bytes = _capture_window_printwindow(self._active_hwnd)
        if pw_bytes:
            return pw_bytes

        logger.warning("All capture methods failed for hwnd=%s", self._active_hwnd)
        return None

    def _enumerate_uia_elements(self) -> list[UIElement]:
        if not self._active_hwnd:
            return []

        try:
            win = self._get_desktop().window(handle=self._active_hwnd)
            elements = []
            idx = 0
            for ctrl in win.descendants():
                if idx >= 500:
                    break
                try:
                    rect = ctrl.rectangle()
                    w, h = rect.width(), rect.height()
                    if w <= 0 or h <= 0:
                        continue
                    control_type = ctrl.element_info.control_type or "Unknown"
                    label = ""
                    with contextlib.suppress(Exception):
                        label = (ctrl.window_text() or "")[:120]
                    elements.append(
                        UIElement(
                            index=idx,
                            role=control_type,
                            label=label,
                            bounds=(rect.left, rect.top, w, h),
                            app=self._active_app,
                            pid=getattr(ctrl.element_info, "process_id", 0),
                            window_id=self._active_hwnd,
                            attributes={"automation_id": getattr(ctrl.element_info, "automation_id", "") or ""},
                        ),
                    )
                    idx += 1
                except Exception:
                    continue
            return elements
        except Exception as e:
            logger.warning("UIA enumeration failed: %s", e)
            return []

    def _resolve_point(self, element: int | None, xy: tuple[int, int] | None) -> tuple[int, int] | None:
        if element is not None:
            if 0 <= element < len(self._element_cache):
                return self._element_cache[element].center()
            return None
        if xy is not None:
            return (xy[0], xy[1])
        return None
