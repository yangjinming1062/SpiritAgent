import json
import logging
import re
import sys
import threading
from typing import Any

from utils import IS_MACOS, IS_WINDOWS, clean_output, is_interrupted

from ..registry import registry, tool_error
from .cu_backend import ActionResult, CaptureResult, ComputerUseBackend, UIElement
from .cu_cua_backend import CuaDriverBackend, cua_driver_binary_available
from .cu_schema import COMPUTER_USE_SCHEMA
from .cu_win_backend import WinBackend
from .helpers import MAX_BASE64_BYTES

logger = logging.getLogger(__name__)

_CAPTURE_MODES = frozenset({"som", "vision", "ax"})
_BUTTONS = frozenset({"left", "right", "middle"})
_SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})

# 键名规范化后再交后端与拦截表。
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
# cmd 归为 win，拦截表与后端同键。
_PLATFORM_KEY_ALIASES = {"cmd": "win"} if IS_WINDOWS else {}
# 同义键名仅用于拦截比对。
_BLOCK_SYNONYMS = {"escape": "esc", "del": "delete"}

# 组合键含任一集合即拒；option 即 alt。
_BLOCKED_KEY_COMBOS: tuple[frozenset[str], ...] = tuple(
    frozenset(combo)
    for combo in (
        # macOS：退出、关闭、最小化、强制退出、截屏、清空废纸篓（delete 即退格键）
        ("cmd", "q"),
        ("cmd", "w"),
        ("cmd", "m"),
        ("cmd", "option", "esc"),
        ("cmd", "shift", "3"),
        ("cmd", "shift", "4"),
        ("cmd", "shift", "5"),
        ("cmd", "shift", "backspace"),
        ("cmd", "shift", "delete"),
        ("cmd", "option", "backspace"),
        ("cmd", "option", "delete"),
        # Windows：关闭窗口、安全桌面、锁屏、显示桌面、资源管理器、运行、设置、最小化全部、高级菜单
        ("option", "f4"),
        ("ctrl", "w"),
        ("ctrl", "option", "delete"),
        ("win", "l"),
        ("win", "d"),
        ("win", "e"),
        ("win", "r"),
        ("win", "i"),
        ("win", "m"),
        ("win", "x"),
    )
)

_BLOCKED_TYPE_PATTERNS = [
    # 管道/分隔符接 shell；DOTALL 覆盖换行。
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"curl\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*bash", re.IGNORECASE | re.DOTALL),
    re.compile(r"wget\s+.*?(?:\|\||&&|[|;])\s*sh\b", re.IGNORECASE | re.DOTALL),
    # 命令替换与参数展开。
    re.compile(r"`[^`]*`", re.DOTALL),
    re.compile(r"\$\([^)]*\)", re.DOTALL),
    re.compile(r"\$\{[^}]*\}", re.DOTALL),
    # 命令后接 shell 一律拦；误拦可改写，漏拦会执行。
    re.compile(r"(?:;|&&|\|\||\|)\s*(?:bash|sh|zsh|ksh)\b", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}", re.IGNORECASE),
]

_backend_lock = threading.RLock()
_backend: ComputerUseBackend | None = None
_backend_shutdown = False


def _canonical_key(name: str) -> str:
    key = _KEY_ALIASES.get(name, name)
    return _PLATFORM_KEY_ALIASES.get(key, key)


def _parse_key_combo(keys: str) -> list[str]:
    """按 '+' 拆分并规范化键名；'-' 是普通按键，不作分隔符。"""
    return [_canonical_key(p) for part in keys.split("+") if (p := part.strip().lower())]


def _canonical_modifiers(value: Any) -> list[str] | None:
    if not value:
        return None
    if not isinstance(value, list):
        raise ValueError("modifiers must be a list of key names")
    return [_canonical_key(m) for item in value if (m := str(item).strip().lower())]


def _blocked_key_combo(keys: list[str]) -> frozenset[str] | None:
    combo = frozenset(_BLOCK_SYNONYMS.get(k, k) for k in keys)
    return next((blocked for blocked in _BLOCKED_KEY_COMBOS if blocked <= combo), None)


def _blocked_type_pattern(text: str) -> str | None:
    return next((pat.pattern for pat in _BLOCKED_TYPE_PATTERNS if pat.search(text)), None)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _point(value: Any) -> tuple[int, int] | None:
    if not value:
        return None
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("coordinates must be [x, y]")
    return int(value[0]), int(value[1])


def _new_backend() -> ComputerUseBackend:
    if IS_MACOS:
        if not cua_driver_binary_available():
            raise RuntimeError("the desktop automation driver (cua-driver) is missing or cannot run on this computer")
        return CuaDriverBackend()
    if IS_WINDOWS:
        return WinBackend()
    raise RuntimeError(f"unsupported platform {sys.platform!r}")


def _get_backend() -> ComputerUseBackend:
    global _backend
    with _backend_lock:
        if _backend_shutdown:
            raise RuntimeError("desktop automation is shutting down")
        if _backend is None:
            backend = _new_backend()
            try:
                backend.start()
            except Exception:
                # 启动失败不缓存。
                try:
                    backend.stop()
                except Exception as exc:
                    logger.warning("Could not stop desktop backend after startup failed: %s", clean_output(str(exc)))
                raise
            _backend = backend
        return _backend


def shutdown_computer_use() -> None:
    """停止已创建的桌面后端，等待当前动作收尾；关闭后不再启动后端。"""
    global _backend, _backend_shutdown
    with _backend_lock:
        _backend_shutdown = True
        backend, _backend = _backend, None
        if backend is not None:
            backend.stop()


def handle_computer_use(args: dict[str, Any], **kwargs: Any) -> str | dict[str, Any]:
    # 已取消则不再开新动作。
    if is_interrupted():
        return tool_error("Interrupted")
    if not (action := str(args.get("action") or "").strip().lower()):
        return tool_error("missing `action`")
    if action == "type" and (pattern := _blocked_type_pattern(str(args.get("text", "")))):
        return tool_error(
            f"blocked pattern in type text: {pattern!r}",
            hint="Dangerous shell patterns cannot be typed via computer_use.",
        )
    if action == "key" and (blocked := _blocked_key_combo(_parse_key_combo(str(args.get("keys") or "")))):
        return tool_error(
            f"blocked key combo: {'+'.join(sorted(blocked))}",
            hint="Destructive system shortcuts are hard-blocked.",
        )
    # 目标状态和后端生命周期共用锁，避免动作执行时停止后端。
    with _backend_lock:
        if is_interrupted():
            return tool_error("Interrupted")
        try:
            backend = _get_backend()
        except Exception as e:
            return tool_error(f"computer_use is unavailable: {e}")
        try:
            return _dispatch(backend, action, args)
        except Exception as e:
            # 预期失败不记堆栈。
            logger.warning(
                "computer_use %s failed: %s",
                action,
                e,
                exc_info=not isinstance(e, LookupError | ValueError),
            )
            return tool_error(f"{action} failed: {e}")


def _dispatch(backend: ComputerUseBackend, action: str, args: dict[str, Any]) -> str | dict[str, Any]:
    capture_after = bool(args.get("capture_after"))
    match action:
        case "capture":
            if (mode := str(args.get("mode") or "som")) not in _CAPTURE_MODES:
                return tool_error(f"bad mode {mode!r}; use som, vision or ax")
            return _capture_response(
                backend.capture(mode=mode, app=args.get("app") or None),
                _coerce_max_elements(args.get("max_elements")),
            )
        case "wait":
            # 超 30s 显式拒绝，防后端截断却看似等满。
            try:
                seconds = float(args.get("seconds", 1.0))
            except (TypeError, ValueError):
                return tool_error("wait: 'seconds' must be a number")
            if not 0 < seconds <= 30:
                return tool_error(f"wait: 'seconds' must be in (0, 30], got {seconds}; loop with shorter waits")
            return _respond(backend, backend.wait(seconds), capture_after)
        case "list_apps":
            apps = backend.list_apps()
            return json.dumps({"apps": apps, "count": len(apps)}, ensure_ascii=False)
        case "focus_app":
            if not (app := args.get("app")):
                return tool_error("focus_app requires `app`")
            return _respond(backend, backend.focus_app(str(app), bool(args.get("bring_to_front"))), capture_after)
        case "click" | "double_click" | "right_click" | "middle_click":
            button = {"right_click": "right", "middle_click": "middle"}.get(action) or str(args.get("button") or "left")
            if button not in _BUTTONS:
                return tool_error(f"bad button {button!r}; use left, right or middle")
            x, y = _point(args.get("coordinate")) or (None, None)
            res = backend.click(
                element=_optional_int(args.get("element")),
                x=x,
                y=y,
                button=button,
                click_count=2 if action == "double_click" else 1,
                modifiers=_canonical_modifiers(args.get("modifiers")),
            )
            return _respond(backend, res, capture_after)
        case "drag":
            if args.get("from_element") is None and not args.get("from_coordinate"):
                return tool_error("drag requires from_coordinate/to_coordinate or from_element/to_element")
            if (button := str(args.get("button") or "left")) not in _BUTTONS:
                return tool_error(f"bad button {button!r}; use left, right or middle")
            res = backend.drag(
                from_element=_optional_int(args.get("from_element")),
                to_element=_optional_int(args.get("to_element")),
                from_xy=_point(args.get("from_coordinate")),
                to_xy=_point(args.get("to_coordinate")),
                button=button,
                modifiers=_canonical_modifiers(args.get("modifiers")),
            )
            return _respond(backend, res, capture_after)
        case "scroll":
            if (direction := str(args.get("direction") or "down")) not in _SCROLL_DIRECTIONS:
                return tool_error(f"bad direction {direction!r}; use up, down, left or right")
            x, y = _point(args.get("coordinate")) or (None, None)
            res = backend.scroll(
                direction=direction,
                amount=int(args.get("amount", 3)),
                element=_optional_int(args.get("element")),
                x=x,
                y=y,
                modifiers=_canonical_modifiers(args.get("modifiers")),
            )
            return _respond(backend, res, capture_after)
        case "type":
            return _respond(backend, backend.type_text(str(args.get("text", ""))), capture_after)
        case "key":
            if not (keys := _parse_key_combo(str(args.get("keys") or ""))):
                return tool_error("key requires `keys`")
            return _respond(backend, backend.key(keys), capture_after)
        case "set_value":
            if (value := args.get("value")) is None:
                return tool_error("set_value requires `value`")
            return _respond(backend, backend.set_value(str(value), _optional_int(args.get("element"))), capture_after)
    return tool_error(f"unknown action {action!r}")


def _coerce_max_elements(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 100
    return n if 1 <= n <= 1000 else 100


def _action_result_payload(res: ActionResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": res.ok, "action": res.action}
    if res.message:
        payload["message"] = clean_output(res.message)
    if res.meta:
        payload["meta"] = res.meta
    return payload


def _respond(backend: ComputerUseBackend, res: ActionResult, capture_after: bool) -> str | dict[str, Any]:
    if capture_after and res.ok:
        try:
            return _capture_response(backend.recapture("som"), action=res)
        except Exception as e:
            logger.warning("follow-up capture failed: %s", e)
            return json.dumps(
                _action_result_payload(res) | {"capture_error": clean_output(str(e))},
                ensure_ascii=False,
            )
    return json.dumps(_action_result_payload(res), ensure_ascii=False)


def _capture_response(
    cap: CaptureResult,
    max_elements: int = 100,
    action: ActionResult | None = None,
) -> str | dict[str, Any]:
    total = len(cap.elements)
    visible = cap.elements[:max_elements]
    lines = []
    if action is not None:
        lines.append(
            f"[{action.action}] ok={action.ok}"
            + (f" message={clean_output(action.message)!r}" if action.message else ""),
        )
    lines.append(
        f"capture mode={cap.mode} {cap.width}x{cap.height}"
        + (f" app={cap.app}" if cap.app else "")
        + (f" window={cap.window_title!r}" if cap.window_title else ""),
    )
    if cap.mode != "vision":
        lines.append(f"{total} interactable element(s):")
        lines.extend(_format_elements(visible))
        if total > len(visible):
            lines.append(f"  ({total - len(visible)} more elements omitted; raise max_elements or pass app= to narrow)")
    if cap.note:
        lines.append(f"note: {cap.note}")

    image = cap.png_b64 if cap.mode != "ax" else None
    if image and len(image) > MAX_BASE64_BYTES:
        lines.append(
            f"(image omitted: {len(image):,} base64 bytes exceeds the {MAX_BASE64_BYTES:,}-byte limit; "
            "pass app= to capture a smaller window or use mode='ax')",
        )
        image = None
    summary = clean_output("\n".join(lines))

    if image:
        return {
            "_multimodal": True,
            "content": [
                {"type": "input_text", "text": summary},
                {"type": "input_image", "image_url": f"data:{cap.image_mime_type};base64,{image}"},
            ],
            "text_summary": summary,
        }

    payload: dict[str, Any] = {
        "mode": cap.mode,
        "width": cap.width,
        "height": cap.height,
        "app": clean_output(cap.app),
        "window_title": clean_output(cap.window_title),
        "elements": [_element_to_dict(e) for e in visible],
        "total_elements": total,
        "summary": summary,
    }
    if action is not None:
        payload |= _action_result_payload(action)
    return json.dumps(payload, ensure_ascii=False)


def _format_elements(elements: list[UIElement]) -> list[str]:
    return [f"  #{e.index} {e.role} {e.label.replace(chr(10), ' ')[:60]!r} @ {e.bounds}" for e in elements]


def _element_to_dict(e: UIElement) -> dict[str, Any]:
    return {"index": e.index, "role": e.role, "label": clean_output(e.label), "bounds": list(e.bounds)}


def _computer_use_available() -> bool:
    """macOS 实际运行 cua-driver，Windows 实际打开截屏设备；只看依赖能否导入不算可用。"""
    if IS_MACOS:
        return cua_driver_binary_available()
    if IS_WINDOWS:
        return WinBackend().is_available()
    return False


registry.register_tool("computer_use", schema=COMPUTER_USE_SCHEMA, check_fn=_computer_use_available)(
    handle_computer_use,
)
registry.register_shutdown_hook(shutdown_computer_use)
