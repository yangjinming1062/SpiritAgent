import json
import logging
import re
import sys
import threading
from typing import Any

from utils import cfg_get, clean_output, is_interrupted, load_config

from ..registry import registry
from .cu_backend import ActionResult, CaptureResult, ComputerUseBackend, UIElement
from .cu_cua_backend import CuaDriverBackend, cua_driver_binary_available
from .cu_schema import COMPUTER_USE_SCHEMA
from .cu_win_backend import WinBackend
from .helpers import _MAX_BASE64_BYTES

logger = logging.getLogger(__name__)

# 稳定前缀，LLM 与下游展示可作为取消标记模式匹配，表明本轮是干净通过 cancel 退出，而不是产生了 assistant 正文
INTERRUPTED_PREFIX = "[INTERRUPTED]"

_BLOCKED_KEY_COMBOS = {
    frozenset({"cmd", "shift", "backspace"}),
    frozenset({"cmd", "option", "backspace"}),
    frozenset({"cmd", "ctrl", "q"}),
    frozenset({"cmd", "shift", "q"}),
    frozenset({"cmd", "option", "shift", "q"}),
    frozenset({"option", "f4"}),
    frozenset({"ctrl", "option", "delete"}),
    frozenset({"win", "l"}),
    frozenset({"win", "d"}),
    # 补——按关键词触发即可覆盖任意组合（带 cmd 的也算）：
    # cmd+q (quit frontmost app)、cmd+w (close window)、cmd+m (minimize window)、
    # cmd+option+esc (force-quit dialog)、cmd+shift+3/4/5 (截屏全屏/窗口/选区)
    frozenset({"cmd", "q"}),
    frozenset({"cmd", "w"}),
    frozenset({"cmd", "m"}),
    frozenset({"cmd", "option", "esc"}),
    frozenset({"cmd", "shift", "3"}),
    frozenset({"cmd", "shift", "4"}),
    frozenset({"cmd", "shift", "5"}),
    # ctrl+w (Windows close window)、alt+f4 (Windows quit via menu)、ctrl+alt+delete
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "alt", "f4"}),
    frozenset({"ctrl", "alt", "delete"}),
    # win 系列：win+e (开资源管理器)、win+r (Run 对话框)、win+i (Settings)、win+l (锁屏)、
    # win+d (显示桌面)、win+m (最小化全部)、win+x (高级菜单)
    frozenset({"win", "e"}),
    frozenset({"win", "r"}),
    frozenset({"win", "i"}),
    frozenset({"win", "m"}),
    frozenset({"win", "x"}),
}

_KEY_ALIASES = {
    "command": "cmd",
    "control": "ctrl",
    "alt": "option",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "⌘": "cmd",
    "⌥": "option",
}
_BLOCKED_TYPE_PATTERNS = [
    # Pipe-to-shell: `curl ... | bash`、`wget ... | sh`，加上备选 shell 命令分隔符 `;`、`&&`、`||`
    # （攻击者替换为这些字符本可绕过黑名单）。re.DOTALL 让 .*? 跨行匹配，因此 `curl http://x\n; bash` 也能命中
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    # 补——之前漏的反向 shell 兜底：
    # 反引号命令替换（`cmd`），$(...) 与 ${...} 参数展开。
    re.compile(r"`[^`]*`", re.DOTALL),
    re.compile(r"\$\([^)]*\)", re.DOTALL),
    re.compile(r"\$\{[^}]*\}", re.DOTALL),
    # 通用 shell 分隔符兜底：任意位置跟 ; bash / && bash / || bash / | bash / | sh
    # （不含 curl/wget 前缀的纯命令 + 分隔符 + shell，例如 `echo evil; bash`）。
    # 误报代价是 model 重试 type；漏报代价是执行 shell — 选更严的。
    re.compile(r"(?:;|&&|\|\||\|)\s*(?:bash|sh|zsh|ksh)\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}", re.IGNORECASE),
]

_backend_lock = threading.Lock()
_backend: ComputerUseBackend | None = None

# 复用 vision_analyze 的 20MB 上限，防止失控的桌面截图（例如带 alpha 通道的全屏 4K）耗尽上下文
_MAX_CAPTURE_BYTES = _MAX_BASE64_BYTES


def _canon_key_combo(keys: str) -> frozenset:
    return frozenset(_KEY_ALIASES.get(p, p) for part in re.split(r"\s*\+\s*", keys) if (p := part.strip().lower()))


def _is_blocked_type(text: str) -> str | None:
    return next((pat.pattern for pat in _BLOCKED_TYPE_PATTERNS if pat.search(text)), None)


def _get_backend() -> ComputerUseBackend:
    global _backend
    with _backend_lock:
        if _backend is None:
            name = cfg_get(load_config(), "computer_use", "backend", default="auto").lower()
            if name in {"cua", "cua-driver"}:
                _backend = CuaDriverBackend()
            elif name == "win":
                _backend = WinBackend()
            elif name == "noop":
                _backend = _NoopBackend()
            elif name in {"auto", ""}:
                if sys.platform == "darwin" and cua_driver_binary_available():
                    _backend = CuaDriverBackend()
                elif sys.platform == "win32":
                    _backend = WinBackend()
                else:
                    _backend = _NoopBackend()
            else:
                raise RuntimeError(f"Unknown computer_use backend={name!r}")
            _backend.start()
        return _backend


class _NoopBackend(ComputerUseBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_available(self) -> bool:
        return False

    def capture(self, mode: str = "som", app: str | None = None) -> CaptureResult:
        self.calls.append(("capture", {"mode": mode, "app": app}))
        return CaptureResult(mode=mode, width=1024, height=768)

    def click(self, **kw) -> ActionResult:
        self.calls.append(("click", kw))
        return ActionResult(ok=True, action="click")

    def drag(self, **kw) -> ActionResult:
        self.calls.append(("drag", kw))
        return ActionResult(ok=True, action="drag")

    def scroll(self, **kw) -> ActionResult:
        self.calls.append(("scroll", kw))
        return ActionResult(ok=True, action="scroll")

    def type_text(self, text: str) -> ActionResult:
        self.calls.append(("type", {"text": text}))
        return ActionResult(ok=True, action="type")

    def key(self, keys: str) -> ActionResult:
        self.calls.append(("key", {"keys": keys}))
        return ActionResult(ok=True, action="key")

    def list_apps(self) -> list[dict[str, Any]]:
        self.calls.append(("list_apps", {}))
        return []

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        self.calls.append(("focus_app", {"app": app, "raise": raise_window}))
        return ActionResult(ok=True, action="focus_app")

    def set_value(self, value: str, element: int | None = None) -> ActionResult:
        self.calls.append(("set_value", {"value": value, "element": element}))
        return ActionResult(ok=True, action="set_value")


def handle_computer_use(args: dict[str, Any], **kwargs) -> Any:
    # 廉价的 interrupt 提前返回：capture/click/scroll 经过 cua driver / Win 后端可能耗时数秒。
    # 此处检查可避免在用户已转移注意力后产生半成品的桌面动作。
    if is_interrupted():
        return json.dumps(
            {"error": "Interrupted", "interrupted": True, "prefix": INTERRUPTED_PREFIX, "returncode": 130},
        )
    if not (action := (args.get("action") or "").strip().lower()):
        return json.dumps({"error": "missing `action`"})

    if action == "type" and (pat := _is_blocked_type(args.get("text", ""))):
        return json.dumps(
            {
                "error": f"blocked pattern in type text: {pat!r}",
                "hint": "Dangerous shell patterns cannot be typed via computer_use.",
            },
        )

    if action == "key":
        combo = _canon_key_combo(args.get("keys", ""))
        if blocked := next((b for b in _BLOCKED_KEY_COMBOS if b.issubset(combo) and len(b) <= len(combo)), None):
            return json.dumps(
                {
                    "error": f"blocked key combo: {sorted(blocked)}",
                    "hint": "Destructive system shortcuts are hard-blocked.",
                },
            )

    try:
        backend = _get_backend()
        if not backend.is_available():
            return json.dumps(
                {"error": "computer_use backend unavailable on this platform; run `spiritagent tools` to enable"},
            )
    except Exception as e:
        return json.dumps(
            {
                "error": f"computer_use backend unavailable: {e}",
                "hint": "Run `spiritagent tools` and enable Computer Use to install cua-driver.",
            },
        )

    try:
        return _dispatch(backend, action, args)
    except Exception as e:
        logger.exception("computer_use %s failed", action)
        return json.dumps({"error": f"{action} failed: {e}"})


def _dispatch(backend: ComputerUseBackend, action: str, args: dict[str, Any]) -> Any:
    capture_after = bool(args.get("capture_after"))
    delivery_mode = str(args.get("delivery_mode") or "background").strip().lower()
    if delivery_mode not in {"background", "foreground"}:
        delivery_mode = "background"
    bring_to_front = bool(args.get("bring_to_front"))

    def _tag(res: ActionResult) -> ActionResult:
        """把 delivery_mode 与默认 verdict 字段盖到结果上，不经过后端 ABC。

        delivery_mode 总是被覆盖 — runner 是唯一知道本次调用 scope（background/foreground）的层。
        escalation 仅在后端保持 dataclass 默认（""）时回填为 "done" / "verify_fresh_state"；
        后端已设置 "escalate" 或其他真实值时予以保留，绝不被 runner 启发式静默覆盖。
        """
        res.delivery_mode = delivery_mode
        if not res.escalation:
            res.escalation = "done" if res.ok else "verify_fresh_state"
        return res

    match action:
        case "capture":
            if (mode := str(args.get("mode", "som"))) not in {"som", "vision", "ax"}:
                return json.dumps({"error": f"bad mode {mode!r}; use som|vision|ax"})
            return _capture_response(
                backend.capture(mode=mode, app=args.get("app")),
                _coerce_max_elements(args.get("max_elements")),
            )
        case "wait":
            # 显式拒绝 > 30s：back-end 会静默 clamp 到 30s，模型却以为真等了 N 秒，
            # 反复 set_value 后发现 UI 没准备好也不知道为什么。
            try:
                seconds = float(args.get("seconds", 1.0))
            except (TypeError, ValueError):
                return json.dumps({"error": "wait: 'seconds' must be a number"})
            if seconds <= 0:
                return json.dumps({"error": "wait: 'seconds' must be > 0"})
            if seconds > 30:
                return json.dumps(
                    {"error": f"wait: 'seconds' {seconds} > max 30; loop with a shorter wait if you need longer"},
                )
            return _maybe_follow_capture(backend, _tag(backend.wait(seconds)), capture_after)
        case "list_apps":
            apps = backend.list_apps()
            return json.dumps({"apps": apps, "count": len(apps)})
        case "focus_app":
            if not (app := args.get("app")):
                return json.dumps({"error": "focus_app requires `app`"})
            # raise_window（遗留）与 bring_to_front（新）是别名
            do_raise = bool(args.get("raise_window")) or bring_to_front
            return _maybe_follow_capture(backend, _tag(backend.focus_app(app, do_raise)), capture_after)
        case "click" | "double_click" | "right_click" | "middle_click":
            button = (
                "right"
                if action == "right_click"
                else "middle"
                if action == "middle_click"
                else args.get("button") or "left"
            )
            coord = args.get("coordinate") or (None, None)
            res = backend.click(
                element=args.get("element"),
                x=coord[0],
                y=coord[1],
                button=button,
                click_count=2 if action == "double_click" else 1,
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "drag":
            if args.get("from_element") is None and not args.get("from_coordinate"):
                return json.dumps({"error": "drag requires from_coordinate/to_coordinate or from_element/to_element"})
            res = backend.drag(
                from_element=args.get("from_element"),
                to_element=args.get("to_element"),
                from_xy=tuple(args["from_coordinate"]) if args.get("from_coordinate") else None,
                to_xy=tuple(args["to_coordinate"]) if args.get("to_coordinate") else None,
                button=args.get("button", "left"),
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "scroll":
            coord = args.get("coordinate") or (None, None)
            res = backend.scroll(
                direction=args.get("direction", "down"),
                amount=int(args.get("amount", 3)),
                element=args.get("element"),
                x=coord[0],
                y=coord[1],
                modifiers=args.get("modifiers"),
            )
            return _maybe_follow_capture(backend, _tag(res), capture_after)
        case "type":
            return _maybe_follow_capture(backend, _tag(backend.type_text(args.get("text", ""))), capture_after)
        case "key":
            return _maybe_follow_capture(backend, _tag(backend.key(args.get("keys", ""))), capture_after)
        case "set_value":
            if (val := args.get("value")) is None:
                return json.dumps({"error": "set_value requires `value`"})
            return _maybe_follow_capture(backend, _tag(backend.set_value(str(val), args.get("element"))), capture_after)
    return json.dumps({"error": f"unknown action {action!r}"})


def _text_response(res: ActionResult) -> str:
    return json.dumps(_action_result_payload(res))


def _sniff_image_mime(b64: str) -> str:
    """尽力通过 base64 magic bytes 猜测 MIME，覆盖 JPEG / PNG / WebP / GIF；无法识别时回退到 image/png，让 LLM 至少看到可渲染的图片标签。cua-driver 0.5.x+ 会在每个 image part 上设置 mimeType — 此兜底仅在该字段缺失或非 cua 后端（WinBackend）时触发。"""
    if not b64:
        return "image/png"
    if b64[:4].startswith("/9j/"):
        return "image/jpeg"
    if b64[:8].startswith("iVBORw0"):
        return "image/png"
    # WebP: RIFF????WEBP
    if b64.startswith("UklGR") and "V0VCUA" in b64[:20]:
        return "image/webp"
    if b64.startswith("R0lGOD"):
        return "image/gif"
    return "image/png"


def _coerce_max_elements(value: Any) -> int:
    try:
        n = int(value)
        return n if 1 <= n <= 1000 else 100
    except (TypeError, ValueError):
        return 100


def _capture_response(cap: CaptureResult, max_elements: int = 100) -> Any:
    total = len(cap.elements)
    visible = cap.elements[:max_elements]
    truncated = max(0, total - len(visible))
    element_index = _format_elements(visible, max_lines=max_elements)
    summary_lines = [
        f"capture mode={cap.mode} {cap.width}x{cap.height}"
        + (f" app={cap.app}" if cap.app else "")
        + (f" window={cap.window_title!r}" if cap.window_title else "")
        + (f" dpi_scale={cap.dpi_scale:.2f}" if cap.dpi_scale != 1.0 else ""),
        f"{total} interactable element(s):",
    ]
    if element_index:
        summary_lines.extend(element_index)
    if truncated:
        summary_lines.append(
            f"  (response truncated to {len(visible)} of {total} elements; raise max_elements or pass app= to narrow)",
        )

    # 一次性丢弃超尺寸 PNG 并提示调用方改为更窄的 app= 或纯文本的 mode='ax'
    if cap.png_b64 and cap.mode != "ax" and (cap.png_bytes_len or 0) > _MAX_CAPTURE_BYTES:
        cap = CaptureResult(
            mode=cap.mode,
            width=cap.width,
            height=cap.height,
            png_b64=None,
            elements=cap.elements,
            app=cap.app,
            window_title=cap.window_title,
            png_bytes_len=cap.png_bytes_len,
        )
        summary_lines.append(
            f"  (PNG dropped — {cap.png_bytes_len:,} bytes exceeds {_MAX_CAPTURE_BYTES:,}-byte cap; pass app= or mode='ax' to narrow the capture)",
        )

    summary = clean_output("\n".join(summary_lines))

    if cap.png_b64 and cap.mode != "ax":
        # 优先采用 cua-driver 通过 image.mimeType 报告的 MIME（cua-driver 0.5.x+）；
        # 旧版本或非 cua 后端（WinBackend）回退到 base64 magic-byte 嗅探
        mime = cap.image_mime_type or _sniff_image_mime(cap.png_b64)
        return {
            "_multimodal": True,
            "content": [
                {"type": "text", "text": summary},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{cap.png_b64}"}},
            ],
            "text_summary": summary,
            "meta": {
                "mode": cap.mode,
                "width": cap.width,
                "height": cap.height,
                "elements": total,
                "png_bytes": cap.png_bytes_len,
            },
        }

    return json.dumps(
        {
            "mode": cap.mode,
            "width": cap.width,
            "height": cap.height,
            "app": clean_output(cap.app) if cap.app else cap.app,
            "window_title": clean_output(cap.window_title) if cap.window_title else cap.window_title,
            "elements": [_element_to_dict(e) for e in visible],
            "total_elements": total,
            "summary": summary,
        }
        | ({"truncated_elements": truncated} if truncated else {})
        | ({"dpi_scale": cap.dpi_scale} if cap.dpi_scale != 1.0 else {}),
    )


def _action_result_payload(res: ActionResult) -> dict[str, Any]:
    """ActionResult 的紧凑 envelope，包含 verdict 字段。在独立文本响应和 follow-up capture merge 上均作为 action header 使用；与 _text_response 对称 — 任何输出 ActionResult 的面都走这个 shape，下游消费者无论后续是否接 capture 都看到一致的字段集合。"""
    payload: dict[str, Any] = {
        "ok": res.ok,
        "action": res.action,
        "delivery_mode": res.delivery_mode,
        "escalation": res.escalation,
    }
    if res.message:
        payload["message"] = clean_output(res.message)
    if res.meta:
        payload["meta"] = res.meta
    if res.effect:
        payload["effect"] = res.effect
    if res.verified:
        payload["verified"] = res.verified
    if res.code:
        payload["code"] = res.code
    return payload


def _maybe_follow_capture(backend: ComputerUseBackend, res: ActionResult, do_capture: bool) -> Any:
    if not do_capture or not res.ok:
        return _text_response(res)
    try:
        cap = backend.capture(mode="som", app=getattr(backend, "_last_app", None))
    except Exception as e:
        logger.warning("follow-up capture failed: %s", e)
        return _text_response(res)

    resp = _capture_response(cap)
    action_payload = _action_result_payload(res)
    if isinstance(resp, dict) and resp.get("_multimodal"):
        safe_message = clean_output(res.message) if res.message else ""
        prefix_parts = [
            f"[{res.action}]",
            f"ok={res.ok}",
            f"delivery_mode={res.delivery_mode}",
            f"escalation={res.escalation}",
        ]
        if safe_message:
            prefix_parts.append(f"message={safe_message!r}")
        prefix = " ".join(prefix_parts)
        resp["content"][0]["text"] = f"{prefix}\n\n{resp['content'][0]['text']}"
        resp["text_summary"] = f"{prefix}\n\n{resp['text_summary']}"
        # 在 envelope 层镜像 verdict 字段，调用方无需解析文本 payload 即可直接获取
        resp.update(action_payload)
        return resp
    try:
        data = json.loads(resp)
    except (TypeError, json.JSONDecodeError):
        data = {"capture": resp}
    return json.dumps({**data, **action_payload})


def _format_elements(elements: list[UIElement], max_lines: int = 40) -> list[str]:
    out = []
    for e in elements[:max_lines]:
        label = e.label.replace("\n", " ")[:60]
        app_suffix = f" [{e.app}]" if e.app else ""
        out.append(f"  #{e.index} {e.role} {label!r} @ {e.bounds}{app_suffix}")
    if len(elements) > max_lines:
        out.append(f"  ... +{len(elements) - max_lines} more (call capture with app= to narrow)")
    return out


def _computer_use_available() -> bool:
    """computer_use 工具在宿主上的可用性。

    macOS：需 cua-driver 二进制在 PATH；Windows：需 pywinauto / mss / pyautogui。
    """
    if sys.platform == "darwin":
        return cua_driver_binary_available()
    if sys.platform == "win32":
        return WinBackend().is_available()
    return False


registry.register_tool(
    "computer_use",
    schema=COMPUTER_USE_SCHEMA,
    check_fn=_computer_use_available,
)(lambda args, **kw: handle_computer_use(args, **kw))


def _element_to_dict(e: UIElement) -> dict[str, Any]:
    return {"index": e.index, "role": e.role, "label": clean_output(e.label), "bounds": list(e.bounds), "app": e.app}
