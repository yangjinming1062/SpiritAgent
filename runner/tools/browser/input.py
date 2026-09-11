"""Input 事件分发：click/type/hover/drag/press/scroll/wait。

共享 KEY_CODE_MAP 和 _parse_numeric_unit；session id 与 ref 解析通过构造时注入的可调用获取。
"""

import contextlib
import json
import re
import sys
import time
from collections.abc import Callable
from typing import Any

KEY_CODE_MAP: dict[str, int] = {
    "enter": 13,
    "tab": 9,
    "escape": 27,
    "esc": 27,
    "backspace": 8,
    "delete": 46,
    "space": 32,
    "arrowup": 38,
    "up": 38,
    "arrowdown": 40,
    "down": 40,
    "arrowleft": 37,
    "left": 37,
    "arrowright": 39,
    "right": 39,
    "pageup": 33,
    "pagedown": 34,
    "home": 36,
    "end": 35,
    "f1": 112,
    "f2": 113,
    "f3": 114,
    "f4": 115,
    "f5": 116,
    "f6": 117,
    "f7": 118,
    "f8": 119,
    "f9": 120,
    "f10": 121,
    "f11": 122,
    "f12": 123,
}


def parse_numeric_unit(
    raw_val: Any,
    default: float,
    *,
    valid_units: tuple[str, ...] = ("s", "ms"),
    allow_negative: bool = False,
) -> float:
    """安全解析带单位数值字符串（支持 s, ms, px 等），返回浮点数。"""
    if raw_val is None:
        return default
    if isinstance(raw_val, bool):
        raise ValueError("Boolean value is not a valid numeric value")
    if isinstance(raw_val, int | float):
        return float(raw_val if allow_negative else abs(raw_val))

    if isinstance(raw_val, str) and raw_val.strip():
        unit_pat = "|".join(re.escape(u) for u in valid_units)
        sign_pat = "[-+]?" if allow_negative else ""
        pattern = rf"^\s*({sign_pat}\d+(?:\.\d+)?)\s*(?:{unit_pat})?\s*$"
        m = re.fullmatch(pattern, raw_val.strip(), re.IGNORECASE)
        if not m:
            raise ValueError(f"Invalid numeric value '{raw_val}'")
        parsed = float(m.group(1))
        if not allow_negative:
            parsed = abs(parsed)
        if raw_val.strip().lower().endswith("ms"):
            parsed = parsed / 1000.0
        return parsed
    return default


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
        """发送一次左键按下+释放：即便 release 失败也再补一次 release，
        防止浏览器把按钮卡在按下状态导致后续 Input 事件失真。"""
        pressed = self._send_cdp(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=sid,
        )
        if not pressed.get("ok"):
            return {"ok": False, "error": pressed.get("error", "Input.dispatchMouseEvent mousePressed failed")}
        release_params = {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}
        released = self._send_cdp("Input.dispatchMouseEvent", release_params, session_id=sid)
        if released.get("ok"):
            return {"ok": True}
        # release 响应失败但事件可能已到达；再补一次 release 防止按钮卡住。
        # retry 成功则视为整体成功（释放事件确实发了），否则回报错误。
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

            # macOS 用 Meta (Command) = bit 8；其它平台用 Control = bit 2。
            # 修过历史上 `4 if darwin` 把 Mac 的 Alt+A 误发，导致 select-all 失效。
            modifiers = 8 if sys.platform == "darwin" else 2
            for evt in (
                {"type": "rawKeyDown", "windowsVirtualKeyCode": 65, "modifiers": modifiers, "key": "a"},
                {"type": "keyUp", "windowsVirtualKeyCode": 65, "modifiers": modifiers, "key": "a"},
                {"type": "rawKeyDown", "windowsVirtualKeyCode": 8, "key": "Backspace"},
                {"type": "keyUp", "windowsVirtualKeyCode": 8, "key": "Backspace"},
            ):
                res = self._send_cdp("Input.dispatchKeyEvent", evt, session_id=sid)
                if not res.get("ok"):
                    return {"ok": False, "error": res.get("error", f"Input.dispatchKeyEvent {evt['type']} failed")}

        if text:
            ins = self._send_cdp("Input.insertText", {"text": text}, session_id=sid)
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
        return {"ok": res.get("ok", False), "direction": d, "pixels": amount}

    def hover_ref(self, ref: str) -> dict[str, Any]:
        try:
            cx, cy, _ = self._resolve_ref(ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()
        res = self._send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy}, session_id=sid)
        return {"ok": res.get("ok", False), "hovered": ref}

    def drag_refs(self, from_ref: str, to_ref: str, *, hold_key: str | None = None, steps: int = 10) -> dict[str, Any]:
        try:
            fx, fy, _ = self._resolve_ref(from_ref)
            tx, ty, _ = self._resolve_ref(to_ref)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        sid = self._session_id_provider()
        modifier_mask = {"shift": 1, "ctrl": 2, "alt": 4}.get((hold_key or "").lower(), 0)
        first_error: dict[str, Any] | None = None
        mouse_released = False
        if modifier_mask:
            res = self._send_cdp(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "modifiers": modifier_mask,
                    "key": hold_key,
                    "code": f"{hold_key.title()}Left",
                    "windowsVirtualKeyCode": {"shift": 16, "ctrl": 17, "alt": 18}.get(hold_key.lower()),
                },
                session_id=sid,
            )
            if not res.get("ok") and first_error is None:
                first_error = res
        try:
            res = self._send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": fx, "y": fy}, session_id=sid)
            if not res.get("ok") and first_error is None:
                first_error = res
            res = self._send_cdp(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": fx, "y": fy, "button": "left", "clickCount": 1},
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
                    {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"},
                    session_id=sid,
                )
                time.sleep(0.02)

            res = self._send_cdp(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1},
                session_id=sid,
            )
            mouse_released = True
            if not res.get("ok") and first_error is None:
                first_error = res
            if first_error is not None:
                return {"ok": False, "error": first_error.get("error", "drag_refs: CDP dispatch failed")}
            return {"ok": True, "from": from_ref, "to": to_ref}
        finally:
            # 鼠标释放 + 修饰键抬起 必须放在同一 finally 里，保证中途异常时
            # 也确保按钮不会被卡在按下状态、修饰键不会被卡在按下状态。
            # 注意: happy path 已经在主体里调用过一次 mouseReleased; 这里只在没释放过的情况下补发。
            if not mouse_released:
                with contextlib.suppress(Exception):
                    self._send_cdp(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1},
                        session_id=sid,
                    )
            if modifier_mask:
                with contextlib.suppress(Exception):
                    self._send_cdp(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyUp",
                            "modifiers": modifier_mask,
                            "key": hold_key,
                            "code": f"{hold_key.title()}Left",
                            "windowsVirtualKeyCode": {"shift": 16, "ctrl": 17, "alt": 18}.get(hold_key.lower()),
                        },
                        session_id=sid,
                    )

    def press_key(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        sid = self._session_id_provider()
        # 未在 ``KEY_CODE_MAP`` 命中的 key 不再静默成功: 静默成功会让模型误以为表单已提交, 是 agent loop 里最糟的失败模式。
        if key.lower() not in KEY_CODE_MAP:
            return {
                "ok": False,
                "error": f"Unknown key {key!r}: not in KEY_CODE_MAP. Use a browser_press variant or check supported keys.",
            }
        vk = KEY_CODE_MAP[key.lower()]
        down = self._send_cdp(
            "Input.dispatchKeyEvent",
            {"type": "rawKeyDown", "windowsVirtualKeyCode": vk, "modifiers": modifiers, "key": key},
            session_id=sid,
        )
        if not down.get("ok"):
            return {"ok": False, "error": down.get("error", "Input.dispatchKeyEvent rawKeyDown failed")}
        up = self._send_cdp(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "windowsVirtualKeyCode": vk, "modifiers": modifiers, "key": key},
            session_id=sid,
        )
        if not up.get("ok"):
            return {"ok": False, "error": up.get("error", "Input.dispatchKeyEvent keyUp failed")}
        return {"ok": True, "pressed": key}

    def wait_for(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_s: float = 10.0,
        cancel_token: Any = None,
    ) -> dict[str, Any]:
        if not selector and not text:
            return {"ok": False, "error": "At least one of `selector` or `text` must be provided"}

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            # 取消令牌触发时立刻退出轮询, 不等下一次 sleep。
            if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
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
            time.sleep(0.2)

        return {"ok": False, "error": f"wait_for timed out after {timeout_s}s"}
