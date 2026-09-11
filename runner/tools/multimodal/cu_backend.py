import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIElement:
    index: int
    role: str
    label: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    app: str = ""
    pid: int = 0
    window_id: int = 0
    element_token: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

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
    png_bytes_len: int = 0
    image_mime_type: str | None = None
    # Windows 上是窗口 DPI / 96。元素 bounds 与截图尺寸按物理像素返回 — 若模型要在
    # pyautogui 期望的逻辑坐标里点击，需自行除以此值。
    dpi_scale: float = 1.0


@dataclass
class ActionResult:
    ok: bool
    action: str
    message: str = ""
    capture: CaptureResult | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # 判定面：
    #   verified   — capture 复核动作是否生效
    #   effect     — 简短人类可读标签，如 "opened file"、"no-op"
    #   escalation — "done" | "verify_fresh_state" | "escalate"
    #   code       — 数值状态码，与 typed_error code 对应
    verified: bool = False
    effect: str = ""
    escalation: str = "done"
    code: int = 0
    # 后端实现标注动作走的是哪条投递通道，runner 调用方据此按通道应用不同的审批范围；默认 "background"
    delivery_mode: str = "background"


# app= 的哨兵值，目标是 OS 桌面壳层（桌面背景 / 任务栏）而非某个具体应用。
# macOS 上解析为 Finder / Dock，Windows 上为 Progman / Shell_TrayWnd。
# 在此集中定义，防止两个平台后端对"什么算哨兵"产生隐性分歧。
DESKTOP_SENTINELS: frozenset[str] = frozenset({"screen", "desktop", "fullscreen", "all"})


class ComputerUseBackend(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult: ...

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
    def key(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def list_apps(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult: ...

    @abstractmethod
    def set_value(self, value: str, element: int | None = None) -> ActionResult: ...

    def wait(self, seconds: float) -> ActionResult:
        time.sleep(max(0.0, min(seconds, 30.0)))
        return ActionResult(ok=True, action="wait", message=f"waited {seconds:.2f}s")
