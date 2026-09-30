import base64
import contextlib
import ctypes
import ctypes.wintypes
import functools
import io
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import psutil
import pyperclip
from PIL import Image, ImageDraw, ImageFont
from utils import IS_WINDOWS

from .cu_backend import DESKTOP_SENTINELS, ActionResult, CaptureResult, ComputerUseBackend, UIElement

if IS_WINDOWS:
    import mss
    import pyautogui
    import pywinauto

logger = logging.getLogger(__name__)

# 桌面自动化不能并发执行。
_serial_lock = threading.RLock()


def _serialized[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with _serial_lock:
            return fn(*args, **kwargs)

    return wrapper


# 规范键名映射到 pyautogui。
_WINDOWS_KEY_MAP = {"option": "alt", "return": "enter"}

_PW_RENDERFULLCONTENT = 0x00000002
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_WHEEL_DELTA = 120
_MAX_UIA_ELEMENTS = 500

# 类名比较统一小写。
_WIN_SHELL_CLASSES = frozenset({"progman", "shell_traywnd"})


def _map_key(key: str) -> str:
    return _WINDOWS_KEY_MAP.get(key, key)


def _enum_windows(callback: Callable[[int], None]) -> None:
    proto = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        callback(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(proto(_cb), 0)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def _window_title(hwnd: int) -> str:
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = ctypes.wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = ctypes.wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    return (rect.left, rect.top, w, h) if w > 0 and h > 0 else None


def _visible_windows_by_pid() -> dict[int, list[int]]:
    """一次枚举全部可见顶层窗口，按 Z 序从前到后归到所属进程。"""
    windows: dict[int, list[int]] = {}

    def collect(hwnd: int) -> None:
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            windows.setdefault(_window_pid(hwnd), []).append(hwnd)

    _enum_windows(collect)
    return windows


def _capture_screen_region(x: int, y: int, w: int, h: int) -> bytes:
    with mss.MSS() as sct:
        img = sct.grab({"left": x, "top": y, "width": w, "height": h})
        return mss.tools.to_png(img.rgb, img.size)


def _capture_window_printwindow(hwnd: int, width: int, height: int) -> bytes | None:
    hdc_window = ctypes.windll.user32.GetDC(hwnd)
    hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_window)
    hbitmap = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    try:
        ctypes.windll.gdi32.SelectObject(hdc_mem, hbitmap)
        if not ctypes.windll.user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
            return None
        return _bitmap_to_png(hdc_mem, hbitmap, width, height)
    finally:
        # 抛错也须释放 GDI 句柄。
        ctypes.windll.gdi32.DeleteObject(hbitmap)
        ctypes.windll.gdi32.DeleteDC(hdc_mem)
        ctypes.windll.user32.ReleaseDC(hwnd, hdc_window)


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

    buf = ctypes.create_string_buffer(width * height * 4)
    ctypes.windll.gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer("RGBA", (width, height), buf.raw, "raw", "BGRA", 0, 1)
    png_io = io.BytesIO()
    img.save(png_io, format="PNG")
    return png_io.getvalue()


def _draw_som_overlay(png_bytes: bytes, elements: list[UIElement]) -> bytes:
    img = Image.open(io.BytesIO(png_bytes))
    draw = ImageDraw.Draw(img)
    try:
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    for elem in elements:
        x, y, w, h = elem.bounds
        draw.rectangle([x, y, x + w, y + h], outline="red", width=2)
        label = str(elem.index)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        draw.rectangle([x, y, x + right - left + 4, y + bottom - top + 4], fill="red")
        draw.text((x + 2, y + 2), label, fill="white", font=font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@contextlib.contextmanager
def _failsafe_suspended() -> Iterator[None]:
    """pyautogui 每次调用前都检查指针是否在屏幕角点；释放按键与鼠标按钮必须绕过该检查，否则会卡在按下状态。"""
    saved = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False
    try:
        yield
    finally:
        pyautogui.FAILSAFE = saved


def _move_pointer(point: tuple[int, int]) -> None:
    """把指针移到目标点；屏幕角点留给用户中止自动化，不作为目标。"""
    if pyautogui.FAILSAFE and point in pyautogui.FAILSAFE_POINTS:
        raise ValueError(
            f"screen point {point} is a screen corner reserved for the user's abort gesture; target a point inside it",
        )
    pyautogui.moveTo(*point, _pause=False)


@contextlib.contextmanager
def _held_keys(modifiers: list[str] | None) -> Iterator[None]:
    """先移动指针再进入：按住修饰键期间不再做会触发角点检查的移动。"""
    pressed: list[str] = []
    try:
        for key in (_map_key(m) for m in modifiers or []):
            pyautogui.keyDown(key)
            pressed.append(key)
        yield
    finally:
        # 抛错也须抬键，防修饰键卡住。
        with _failsafe_suspended():
            for key in reversed(pressed):
                try:
                    pyautogui.keyUp(key, _pause=False)
                except Exception as e:
                    logger.warning("failed to release %s: %s", key, e)


def _failed(action: str, error: Exception) -> ActionResult:
    if isinstance(error, pyautogui.FailSafeException):
        return ActionResult(
            ok=False,
            action=action,
            message="Stopped: the mouse pointer is in a screen corner, which the user can use to abort desktop control.",
        )
    return ActionResult(ok=False, action=action, message=str(error))


class WinBackend(ComputerUseBackend):
    """Windows 后端：UIA 枚举、mss 截图、pyautogui 键鼠；坐标为目标窗口截图像素。"""

    def __init__(self) -> None:
        self._hwnd: int | None = None
        self._app = ""
        self._elements: list[UIElement] = []
        # 与 _elements 同序，供 set_value。
        self._controls: list[Any] = []
        self._desktop: Any = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self._hwnd = None
        self._elements, self._controls = [], []
        self._desktop = None

    def is_available(self) -> bool:
        if not IS_WINDOWS:
            return False
        with mss.MSS() as sct:
            return len(sct.monitors) > 1

    @_serialized
    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        if app and app.lower() in DESKTOP_SENTINELS:
            hwnd, name = self._find_shell_window()
            if hwnd is None:
                raise LookupError(f"no desktop or taskbar window found for app={app!r}")
        elif app:
            hwnd, name = self._find_window_by_app(app)
            if hwnd is None:
                raise LookupError(f"no window matched app={app!r}; call list_apps to see available apps")
        else:
            if not (hwnd := ctypes.windll.user32.GetForegroundWindow()):
                raise LookupError("no foreground window to capture")
            name = _window_title(hwnd)
        self._set_target(hwnd, name)
        return self._capture_target(mode)

    @_serialized
    def recapture(self, mode: str = "som") -> CaptureResult:
        if self._hwnd is None:
            raise LookupError("no window has been captured yet; call capture first")
        return self._capture_target(mode)

    def _set_target(self, hwnd: int, name: str) -> None:
        if hwnd != self._hwnd:
            self._elements, self._controls = [], []
        self._hwnd, self._app = hwnd, name

    def _capture_target(self, mode: str) -> CaptureResult:
        hwnd = self._hwnd
        if hwnd is None or not ctypes.windll.user32.IsWindow(hwnd):
            self._hwnd = None
            raise LookupError("the target window no longer exists; capture again")
        if (rect := _window_rect(hwnd)) is None:
            raise LookupError("the target window has no visible area; it may be minimized")
        left, top, width, height = rect
        capture = CaptureResult(mode=mode, width=width, height=height, app=self._app, window_title=_window_title(hwnd))

        self._elements, self._controls = [], []
        if mode != "vision":
            try:
                self._elements, self._controls = self._enumerate_uia_elements(hwnd, rect)
            except Exception as e:
                logger.warning("UIA enumeration failed: %s", e)
                capture.note = f"the element list is unavailable for this window: {e}"
            capture.elements = self._elements

        if mode != "ax":
            try:
                png = _capture_screen_region(left, top, width, height)
            except Exception as e:
                logger.debug("mss capture failed, falling back to PrintWindow: %s", e)
                png = _capture_window_printwindow(hwnd, width, height)
            if png is None:
                raise RuntimeError("could not capture the window image")
            if mode == "som" and self._elements:
                png = _draw_som_overlay(png, self._elements)
            capture.png_b64 = base64.b64encode(png).decode()
        return capture

    def _enumerate_uia_elements(
        self,
        hwnd: int,
        rect: tuple[int, int, int, int],
    ) -> tuple[list[UIElement], list[Any]]:
        left, top, width, height = rect
        elements: list[UIElement] = []
        controls: list[Any] = []
        for ctrl in self._get_desktop().window(handle=hwnd).iter_descendants():
            if len(elements) >= _MAX_UIA_ELEMENTS:
                break
            try:
                r = ctrl.rectangle()
                x, y, w, h = r.left - left, r.top - top, r.width(), r.height()
                # 跳过零尺寸/窗外元素。
                if w <= 0 or h <= 0 or x + w <= 0 or y + h <= 0 or x >= width or y >= height:
                    continue
                label = ""
                with contextlib.suppress(Exception):
                    label = (ctrl.window_text() or "")[:120]
                elements.append(
                    UIElement(
                        index=len(elements),
                        role=ctrl.element_info.control_type or "Unknown",
                        label=label,
                        bounds=(x, y, w, h),
                    ),
                )
                controls.append(ctrl)
            except Exception:
                # 枚举期间控件可能消失。
                continue
        return elements, controls

    def _to_screen(self, x: int, y: int) -> tuple[int, int]:
        if self._hwnd is None or (rect := _window_rect(self._hwnd)) is None:
            raise LookupError("no captured window to place the coordinates in; call capture first")
        return rect[0] + x, rect[1] + y

    def _screen_point(self, element: int | None, xy: tuple[int, int] | None) -> tuple[int, int]:
        if element is not None:
            if not 0 <= element < len(self._elements):
                raise IndexError(
                    f"element {element} is not in the latest capture ({len(self._elements)} elements); capture again",
                )
            return self._to_screen(*self._elements[element].center())
        if xy is not None:
            return self._to_screen(*xy)
        raise ValueError("an element or coordinate is required")

    def _foreground_refusal(self, action: str) -> ActionResult | None:
        """已选定目标但它不在前台时拒绝键盘输入：pyautogui 的按键总是发给前台窗口。"""
        if self._hwnd is None:
            return None
        foreground = ctypes.windll.user32.GetForegroundWindow()
        if foreground and _window_pid(foreground) == _window_pid(self._hwnd):
            return None
        return ActionResult(
            ok=False,
            action=action,
            message=f"{self._app or 'The target window'} is not in the foreground, so keystrokes would go to another "
            "window. Call focus_app with bring_to_front=true first.",
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
        try:
            sx, sy = self._screen_point(element, (x, y) if x is not None and y is not None else None)
            _move_pointer((sx, sy))
            with _held_keys(modifiers):
                pyautogui.click(clicks=click_count, button=button, _pause=False)
        except Exception as e:
            return _failed("click", e)
        return ActionResult(ok=True, action="click", message=f"clicked {button} x{click_count} at screen ({sx}, {sy})")

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
        try:
            start = self._screen_point(from_element, from_xy)
            end = self._screen_point(to_element, to_xy)
            if pyautogui.FAILSAFE and end in pyautogui.FAILSAFE_POINTS:
                raise ValueError(f"screen point {end} is a screen corner reserved for the user's abort gesture")
            _move_pointer(start)
            with _held_keys(modifiers):
                pyautogui.mouseDown(button=button, _pause=False)
                try:
                    pyautogui.dragTo(*end, duration=0.5, button=button, mouseDownUp=False, _pause=False)
                finally:
                    # 中止拖动也须松开按钮。
                    with _failsafe_suspended():
                        pyautogui.mouseUp(button=button, _pause=False)
        except Exception as e:
            return _failed("drag", e)
        return ActionResult(ok=True, action="drag", message=f"dragged screen {start} -> {end}")

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
        ticks = max(1, min(50, amount))
        delta = ticks * _WHEEL_DELTA * (1 if direction in {"up", "right"} else -1)
        flag = _MOUSEEVENTF_WHEEL if direction in {"up", "down"} else _MOUSEEVENTF_HWHEEL
        try:
            xy = (x, y) if x is not None and y is not None else None
            if element is not None or xy:
                sx, sy = self._screen_point(element, xy)
                _move_pointer((sx, sy))
            else:
                sx, sy = pyautogui.position()
            with _held_keys(modifiers):
                # 直接发整格滚轮，绕开 pyautogui 1/120 限制。
                ctypes.windll.user32.mouse_event(flag, 0, 0, delta, 0)
        except Exception as e:
            return _failed("scroll", e)
        return ActionResult(ok=True, action="scroll", message=f"scrolled {direction} x{ticks} at screen ({sx}, {sy})")

    @_serialized
    def type_text(self, text: str) -> ActionResult:
        if refusal := self._foreground_refusal("type"):
            return refusal
        try:
            if text.isascii():
                pyautogui.write(text, interval=0.02)
            else:
                # 非 ASCII 走剪贴板，事后尽力恢复。
                previous = None
                with contextlib.suppress(Exception):
                    previous = pyperclip.paste()
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                if previous is not None:
                    time.sleep(0.5)
                    with contextlib.suppress(Exception):
                        pyperclip.copy(previous)
        except Exception as e:
            return _failed("type", e)
        return ActionResult(ok=True, action="type", message=f"typed {len(text)} chars")

    @_serialized
    def key(self, keys: list[str]) -> ActionResult:
        mapped = [_map_key(k) for k in keys]
        if invalid := [k for k in mapped if not pyautogui.isValidKey(k)]:
            return ActionResult(ok=False, action="key", message=f"Unknown key name(s) on Windows: {invalid}.")
        if refusal := self._foreground_refusal("key"):
            return refusal
        try:
            pyautogui.hotkey(*mapped)
        except Exception as e:
            return _failed("key", e)
        return ActionResult(ok=True, action="key", message=f"pressed {'+'.join(mapped)}")

    @_serialized
    def list_apps(self) -> list[dict[str, Any]]:
        apps = []
        for pid, hwnds in _visible_windows_by_pid().items():
            try:
                name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            apps.append({"name": name, "pid": pid, "window_count": len(hwnds)})
        return apps

    @_serialized
    def focus_app(self, app: str, bring_to_front: bool = False) -> ActionResult:
        hwnd, name = self._find_window_by_app(app)
        if hwnd is None:
            return ActionResult(ok=False, action="focus_app", message=f"No window found for app '{app}'.")
        self._set_target(hwnd, name)
        if not bring_to_front:
            return ActionResult(
                ok=True,
                action="focus_app",
                message=f"Selected {name} as the target without raising it. Clicks land on whatever is visible at the "
                "target position; type and key are refused until the window is in the foreground "
                "(focus_app with bring_to_front=true).",
            )
        try:
            self._get_desktop().window(handle=hwnd).set_focus()
        except Exception as e:
            return ActionResult(ok=False, action="focus_app", message=f"Failed to raise {name}: {e}")
        return ActionResult(ok=True, action="focus_app", message=f"Raised and focused {name}.")

    @_serialized
    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        if element is None:
            return ActionResult(ok=False, action="set_value", message="set_value requires element.")
        if not 0 <= element < len(self._controls):
            return ActionResult(
                ok=False,
                action="set_value",
                message=f"element {element} is not in the latest capture ({len(self._controls)} elements); capture again",
            )
        control, role = self._controls[element], self._elements[element].role.lower()
        try:
            if "combo" in role or "list" in role:
                control.select(value)
            elif "slider" in role or "scroll" in role:
                control.set_value(value)
            else:
                control.set_edit_text(value)
        except Exception as e:
            return _failed("set_value", e)
        return ActionResult(ok=True, action="set_value", message=f"Set value on element #{element}")

    def _get_desktop(self) -> Any:
        if self._desktop is None:
            self._desktop = pywinauto.Desktop(backend="uia")
        return self._desktop

    def _find_window_by_app(self, app: str) -> tuple[int | None, str]:
        """先按窗口标题，再按进程名匹配；同一进程取 Z 序最前的可见窗口。"""
        needle = app.lower()
        for win in self._get_desktop().windows(visible_only=True):
            with contextlib.suppress(Exception):
                if needle in (title := win.window_text()).lower():
                    return win.handle, title
        for pid, hwnds in _visible_windows_by_pid().items():
            try:
                name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if needle in name.lower():
                return hwnds[0], name
        return None, ""

    def _find_shell_window(self) -> tuple[int | None, str]:
        """Z 序最前的可见 Progman / Shell_TrayWnd，供 app='desktop' 等哨兵使用。"""
        found: list[tuple[int, str]] = []

        def collect(hwnd: int) -> None:
            if ctypes.windll.user32.IsWindowVisible(hwnd) and (cls := _class_name(hwnd)).lower() in _WIN_SHELL_CLASSES:
                found.append((hwnd, cls))

        _enum_windows(collect)
        return found[0] if found else (None, "")
