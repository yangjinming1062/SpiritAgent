import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIElement:
    index: int
    role: str
    label: str = ""
    # Windows 为截图像素（窗口左上角为原点）；macOS 原样取 cua-driver 报告的 frame。
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bounds
        return x + w // 2, y + h // 2


@dataclass
class CaptureResult:
    mode: str
    width: int
    height: int
    png_b64: str | None = None
    elements: list[UIElement] = field(default_factory=list)
    app: str = ""
    window_title: str = ""
    image_mime_type: str = "image/png"
    # 截图或元素不完整的原因，随结果交给模型。
    note: str = ""


@dataclass
class ActionResult:
    ok: bool
    action: str
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


# app= 的哨兵值，目标是 OS 桌面壳层（桌面背景 / 任务栏）而非某个具体应用。
# macOS 上解析为 Finder / Dock，Windows 上为 Progman / Shell_TrayWnd。
DESKTOP_SENTINELS: frozenset[str] = frozenset({"screen", "desktop", "fullscreen", "all"})


class ComputerUseBackend(ABC):
    """坐标一律是最近一次截图内的像素（窗口左上角为原点）；key 与 modifiers 收到的是规范化后的键名。"""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        """app 为空时截取前台窗口；截取对象成为后续输入动作的目标。"""

    @abstractmethod
    def recapture(self, mode: str = "som") -> CaptureResult:
        """重新截取当前目标窗口，不重新选择目标。"""

    @abstractmethod
    def click(
        self,
        *,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def drag(
        self,
        *,
        from_element: int | None = None,
        to_element: int | None = None,
        from_xy: tuple[int, int] | None = None,
        to_xy: tuple[int, int] | None = None,
        button: str = "left",
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: int | None = None,
        x: int | None = None,
        y: int | None = None,
        modifiers: list[str] | None = None,
    ) -> ActionResult: ...

    @abstractmethod
    def type_text(self, text: str) -> ActionResult: ...

    @abstractmethod
    def key(self, keys: list[str]) -> ActionResult: ...

    @abstractmethod
    def list_apps(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def focus_app(self, app: str, bring_to_front: bool = False) -> ActionResult: ...

    @abstractmethod
    def set_value(self, value: str, element: int | None = None) -> ActionResult: ...

    def wait(self, seconds: float) -> ActionResult:
        time.sleep(max(0.0, min(seconds, 30.0)))
        return ActionResult(ok=True, action="wait", message=f"waited {seconds:.2f}s")
