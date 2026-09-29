"""Input 事件分发：click/type/hover/drag/press/scroll/wait。

session id 与 ref 解析通过构造时注入的可调用获取。
"""

import contextlib
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

# 模型可用的按键名（不区分大小写）→ (DOM key, DOM code, windowsVirtualKeyCode, text)。
# 带 text 的按键须以 keyDown 发送才会产生字符输入，例如 Enter 提交表单、Space 激活控件。
_KEYS: dict[str, tuple[str, str, int, str]] = {
    "enter": ("Enter", "Enter", 13, "\r"),
    "tab": ("Tab", "Tab", 9, ""),
    "escape": ("Escape", "Escape", 27, ""),
    "esc": ("Escape", "Escape", 27, ""),
    "backspace": ("Backspace", "Backspace", 8, ""),
    "delete": ("Delete", "Delete", 46, ""),
    "space": (" ", "Space", 32, " "),
    "arrowup": ("ArrowUp", "ArrowUp", 38, ""),
    "up": ("ArrowUp", "ArrowUp", 38, ""),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, ""),
    "down": ("ArrowDown", "ArrowDown", 40, ""),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, ""),
    "left": ("ArrowLeft", "ArrowLeft", 37, ""),
    "arrowright": ("ArrowRight", "ArrowRight", 39, ""),
    "right": ("ArrowRight", "ArrowRight", 39, ""),
    "pageup": ("PageUp", "PageUp", 33, ""),
    "pagedown": ("PageDown", "PageDown", 34, ""),
    "home": ("Home", "Home", 36, ""),
    "end": ("End", "End", 35, ""),
    **{f"f{n}": (f"F{n}", f"F{n}", 111 + n, "") for n in range(1, 13)},
}

# drag 的 hold_key → (CDP modifiers 位, DOM key, DOM code, windowsVirtualKeyCode)
_DRAG_MODIFIERS: dict[str, tuple[int, str, str, int]] = {
    "shift": (8, "Shift", "ShiftLeft", 16),
    "ctrl": (2, "Control", "ControlLeft", 17),
    "alt": (1, "Alt", "AltLeft", 18),
}


def parse_numeric_unit(
    raw_val: Any,
    default: float,
    *,
    valid_units: tuple[str, ...] = ("s", "ms"),
) -> float:
    """安全解析带单位数值字符串（支持 s, ms, px 等），返回非负浮点数。"""
    if raw_val is None:
        return default
    if isinstance(raw_val, bool):
        raise ValueError("Boolean value is not a valid numeric value")
    if isinstance(raw_val, int | float):
        return abs(float(raw_val))

    if isinstance(raw_val, str) and raw_val.strip():
        unit_pat = "|".join(re.escape(u) for u in valid_units)
        pattern = rf"^\s*(\d+(?:\.\d+)?)\s*(?:{unit_pat})?\s*$"
        m = re.fullmatch(pattern, raw_val.strip(), re.IGNORECASE)
        if not m:
            raise ValueError(f"Invalid numeric value '{raw_val}'")
        parsed = abs(float(m.group(1)))
        if raw_val.strip().lower().endswith("ms"):
            parsed = parsed / 1000.0
        return parsed
    return default


def _dialog_opened_by(res: dict[str, Any]) -> dict[str, Any] | None:
    """send_cdp 结果表明本次调用触发了弹窗（动作已生效）时返回该弹窗。"""
    return res.get("dialog") if res.get("dialog_opened_by_call") else None


SendCdpFn = Callable[..., dict[str, Any]]
EvaluateRuntimeFn = Callable[..., dict[str, Any]]
ResolveRefFn = Callable[..., tuple[float, float, str | None]]
SessionIdProvider = Callable[[], str | None]
WaitStableFn = Callable[..., bool]


class InputDispatch:
    def __init__(
        self,
        *,
        send_cdp: SendCdpFn,
        evaluate_runtime: EvaluateRuntimeFn,
        resolve_ref: ResolveRefFn,
        session_id_provider: SessionIdProvider,
        wait_for_page_stable: WaitStableFn,
    ) -> None:
        self._send_cdp = send_cdp
        self._evaluate_runtime = evaluate_runtime
        self._resolve_ref = resolve_ref
        self._session_id_provider = session_id_provider
        self._wait_for_page_stable = wait_for_page_stable

    def _dispatch_left_click(self, sid: str | None, x: float, y: float) -> dict[str, Any]:
        """发送一次左键按下+释放。点击本身触发了弹窗时仍视为成功并带回 ``dialog``，避免调用方重复点击；
        调用前已有弹窗时点击不会送达，按失败返回。release 失败时再补一次 release，防止按钮卡在按下状态。"""
        pressed = self._send_cdp(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=sid,
        )
        if opened := _dialog_opened_by(pressed):
            return {"ok": True, "dialog": opened}
        if not pressed.get("ok"):
            return {"ok": False, "error": pressed.get("error", "Input.dispatchMouseEvent mousePressed failed")}
        release_params = {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}
        released = self._send_cdp("Input.dispatchMouseEvent", release_params, session_id=sid)
        if opened := _dialog_opened_by(released):
            return {"ok": True, "dialog": opened}
        if released.get("ok"):
            return {"ok": True}
        if released.get("dialog"):
            return {"ok": False, "error": released.get("error", "Input.dispatchMouseEvent mouseReleased failed")}
        retry = self._send_cdp("Input.dispatchMouseEvent", release_params, session_id=sid, timeout=2.0)
        if retry.get("ok"):
            return {"ok": True, "warning": released.get("error", "mouseReleased response lost; retry succeeded")}
        return {"ok": False, "error": released.get("error", "Input.dispatchMouseEvent mouseReleased failed")}

    def click_ref(self, ref: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        try:
            cx, cy, _ = self._resolve_ref(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()
        res = self._dispatch_left_click(sid, cx, cy)
        if not res.get("ok"):
            return res
        if dialog := res.get("dialog"):
            return {"ok": True, "clicked": ref, "dialog": dialog}
        if wait_stable:
            self._wait_for_page_stable(timeout_s=timeout_s)
        return {"ok": True, "clicked": ref}

    def type_ref(self, ref: str, text: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]:
        """先聚焦并清空元素，再输入新文本。"""

        try:
            cx, cy, obj_id = self._resolve_ref(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()

        js_cleared = False
        if obj_id:
            eval_clear = self._send_cdp(
                "Runtime.callFunctionOn",
                {
                    "objectId": obj_id,
                    "functionDeclaration": (
                        "function() {"
                        "  this.focus();"
                        "  if (typeof this.select === 'function') { this.select(); return true; }"
                        "  if ('value' in this) { this.value = ''; return true; }"
                        "  if (this.isContentEditable) {"
                        "    const r = document.createRange(); r.selectNodeContents(this);"
                        "    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
                        "    document.execCommand('delete', false); return true;"
                        "  }"
                        "  return false;"
                        "}"
                    ),
                    "returnByValue": True,
                },
                session_id=sid,
            )
            if eval_clear.get("ok"):
                js_cleared = bool(eval_clear["result"].get("result", {}).get("value"))

        if not js_cleared:
            res = self._dispatch_left_click(sid, cx, cy)
            if not res.get("ok"):
                return res

            if not obj_id:
                eval_active = self._send_cdp(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "(() => {"
                            "  const el = document.activeElement;"
                            "  if (!el || el === document.body || el === document.documentElement) return false;"
                            "  const tag = el.tagName.toLowerCase();"
                            "  if (['input', 'textarea'].includes(tag)) return true;"
                            "  if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return true;"
                            "  const role = (el.getAttribute('role') || '').toLowerCase();"
                            "  return ['textbox', 'searchbox', 'combobox'].includes(role);"
                            "})()"
                        ),
                        "returnByValue": True,
                    },
                    session_id=sid,
                )
                is_active_input = bool(eval_active.get("result", {}).get("result", {}).get("value"))
                if not is_active_input:
                    return {"ok": False, "error": f"Target at '{ref}' did not focus an editable input field"}

            # CDP modifiers 位：Alt=1、Ctrl=2、Meta=4、Shift=8。macOS 的编辑快捷键不经合成按键事件触发，
            # 须随 keyDown 附带 selectAll 编辑命令。
            modifiers = 4 if sys.platform == "darwin" else 2
            for evt in (
                {
                    "type": "rawKeyDown",
                    "windowsVirtualKeyCode": 65,
                    "modifiers": modifiers,
                    "key": "a",
                    "code": "KeyA",
                    "commands": ["selectAll"],
                },
                {"type": "keyUp", "windowsVirtualKeyCode": 65, "modifiers": modifiers, "key": "a", "code": "KeyA"},
                {"type": "rawKeyDown", "windowsVirtualKeyCode": 8, "key": "Backspace"},
                {"type": "keyUp", "windowsVirtualKeyCode": 8, "key": "Backspace"},
            ):
                res = self._send_cdp("Input.dispatchKeyEvent", evt, session_id=sid)
                if not res.get("ok"):
                    return {"ok": False, "error": res.get("error", f"Input.dispatchKeyEvent {evt['type']} failed")}

        if text:
            ins = self._send_cdp("Input.insertText", {"text": text}, session_id=sid)
            if opened := _dialog_opened_by(ins):
                return {"ok": True, "typed": text, "ref": ref, "dialog": opened}
            if not ins.get("ok"):
                return {"ok": False, "error": ins.get("error", "Input.insertText failed")}

        if wait_stable:
            self._wait_for_page_stable(timeout_s=timeout_s)
        return {"ok": True, "typed": text, "ref": ref}

    def scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]:
        sid = self._session_id_provider()
        d = (direction or "down").strip().lower()
        amount = max(0, min(int(pixels), 5000))
        if d == "up":
            delta_x, delta_y = 0, -amount
        elif d == "left":
            delta_x, delta_y = -amount, 0
        elif d == "right":
            delta_x, delta_y = amount, 0
        elif d == "down":
            delta_x, delta_y = 0, amount
        else:
            return {"ok": False, "error": f"Invalid scroll direction '{direction}' (expected down/up/left/right)"}
        res = self._send_cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": 100, "y": 100, "deltaX": delta_x, "deltaY": delta_y},
            session_id=sid,
        )
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Input.dispatchMouseEvent mouseWheel failed")}
        return {"ok": True, "direction": d, "pixels": amount}

    def hover_ref(self, ref: str) -> dict[str, Any]:
        try:
            cx, cy, _ = self._resolve_ref(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()
        res = self._send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy}, session_id=sid)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Input.dispatchMouseEvent mouseMoved failed")}
        return {"ok": True, "hovered": ref}

    def drag_refs(self, from_ref: str, to_ref: str, *, hold_key: str | None = None, steps: int = 10) -> dict[str, Any]:
        try:
            fx, fy, _ = self._resolve_ref(from_ref)
            tx, ty, _ = self._resolve_ref(to_ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()
        modifier = _DRAG_MODIFIERS.get((hold_key or "").lower())
        mask = modifier[0] if modifier else 0
        first_error: dict[str, Any] | None = None
        mouse_released = False
        if modifier:
            _, key, code, vk = modifier
            res = self._send_cdp(
                "Input.dispatchKeyEvent",
                {"type": "rawKeyDown", "modifiers": mask, "key": key, "code": code, "windowsVirtualKeyCode": vk},
                session_id=sid,
            )
            if not res.get("ok"):
                first_error = res
        try:
            res = self._send_cdp(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": fx, "y": fy, "modifiers": mask},
                session_id=sid,
            )
            if not res.get("ok") and first_error is None:
                first_error = res
            res = self._send_cdp(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": fx, "y": fy, "button": "left", "clickCount": 1, "modifiers": mask},
                session_id=sid,
            )
            if not res.get("ok") and first_error is None:
                first_error = res

            for i in range(1, steps + 1):
                if first_error is not None:
                    break
                curr_x = fx + (tx - fx) * (i / steps)
                curr_y = fy + (ty - fy) * (i / steps)
                self._send_cdp(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left", "modifiers": mask},
                    session_id=sid,
                )
                time.sleep(0.02)

            res = self._send_cdp(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1, "modifiers": mask},
                session_id=sid,
            )
            mouse_released = True
            if not res.get("ok") and first_error is None:
                first_error = res
            if first_error is not None:
                return {"ok": False, "error": first_error.get("error", "drag_refs: CDP dispatch failed")}
            return {"ok": True, "from": from_ref, "to": to_ref}
        finally:
            # 中途异常时也要补发鼠标释放与修饰键抬起，避免按键卡在按下状态；正常路径已释放过鼠标。
            if not mouse_released:
                with contextlib.suppress(Exception):
                    self._send_cdp(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1},
                        session_id=sid,
                    )
            if modifier:
                _, key, code, vk = modifier
                with contextlib.suppress(Exception):
                    self._send_cdp(
                        "Input.dispatchKeyEvent",
                        {"type": "keyUp", "key": key, "code": code, "windowsVirtualKeyCode": vk},
                        session_id=sid,
                    )

    def press_key(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        sid = self._session_id_provider()
        # 未知按键必须报错：静默成功会让模型误以为表单已提交。
        spec = _KEYS.get(key.strip().lower())
        if spec is None:
            supported = ", ".join(sorted({"Space" if k == " " else k for k, *_ in _KEYS.values()}))
            return {"ok": False, "error": f"Unsupported key {key!r}. Supported keys: {supported}."}
        dom_key, code, vk, text = spec
        down_evt: dict[str, Any] = {
            "type": "keyDown" if text else "rawKeyDown",
            "windowsVirtualKeyCode": vk,
            "modifiers": modifiers,
            "key": dom_key,
            "code": code,
        }
        if text:
            down_evt |= {"text": text, "unmodifiedText": text}
        down = self._send_cdp("Input.dispatchKeyEvent", down_evt, session_id=sid)
        if opened := _dialog_opened_by(down):
            return {"ok": True, "pressed": key, "dialog": opened}
        if not down.get("ok"):
            return {"ok": False, "error": down.get("error", f"Input.dispatchKeyEvent {down_evt['type']} failed")}
        up = self._send_cdp(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "windowsVirtualKeyCode": vk, "modifiers": modifiers, "key": dom_key, "code": code},
            session_id=sid,
        )
        if opened := _dialog_opened_by(up):
            return {"ok": True, "pressed": key, "dialog": opened}
        if not up.get("ok"):
            return {"ok": False, "error": up.get("error", "Input.dispatchKeyEvent keyUp failed")}
        return {"ok": True, "pressed": key}

    def wait_for(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_s: float = 10.0,
        cancel_token: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not selector and not text:
            return {"ok": False, "error": "At least one of `selector` or `text` must be provided"}

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if cancel_token is not None and cancel_token.is_set():
                return {"ok": False, "error": "Caller cancelled wait_for", "cancelled": True}
            last_error: dict[str, Any] | None = None
            if selector:
                safe_sel = json.dumps(selector)
                res = self._evaluate_runtime(f"Boolean(document.querySelector({safe_sel}))")
                if res.get("ok") and res.get("result"):
                    return {"ok": True, "matched": "selector", "value": selector}
                if res.get("ok") is False:
                    last_error = res
            if text:
                safe_txt = json.dumps(text.lower())
                res = self._evaluate_runtime(f"(document.body.innerText || '').toLowerCase().includes({safe_txt})")
                if res.get("ok") and res.get("result"):
                    return {"ok": True, "matched": "text", "value": text}
                if res.get("ok") is False:
                    last_error = res
            # 单次 eval 失败不应立即放弃：若同时给了 selector+text，下一轮重试即可绕过瞬时 CDP 抖动。
            # 仅当 deadline 用尽且从未匹配时才回报 last_error。
            if time.monotonic() >= deadline:
                if last_error is not None:
                    return {"ok": False, "error": last_error.get("error", "wait_for eval failed")}
                break
            if cancel_token is not None:
                cancel_token.wait(0.2)
            else:
                time.sleep(0.2)

        return {"ok": False, "error": f"wait_for timed out after {timeout_s}s"}
