import threading
import time
from typing import Any, Protocol

from spirit_agent_runner.features.browser.input import parse_numeric_unit


class BrowserActions(Protocol):
    def click_ref(self, ref: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]: ...
    def type_ref(self, ref: str, text: str, *, wait_stable: bool = True, timeout_s: float = 0.2) -> dict[str, Any]: ...
    def press_key(self, key: str, modifiers: int = 0) -> dict[str, Any]: ...
    def hover_ref(self, ref: str) -> dict[str, Any]: ...
    def scroll_page(self, direction: str = "down", pixels: int = 500) -> dict[str, Any]: ...
    def select_ref(
        self,
        ref: str,
        *,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]: ...
    def wait_for_page_stable(self, timeout_s: float = 2.0) -> bool: ...


class BatchOperations:
    def __init__(self, operations: BrowserActions) -> None:
        self._operations = operations

    def execute_batch(
        self,
        actions: list[dict[str, Any]],
        wait_between_ms: int = 100,
        cancel_token: threading.Event | None = None,
    ) -> dict[str, Any]:
        """按序执行一组浏览器操作；任一步失败即停止，全部成功后等待页面沉降。"""
        if not actions or not isinstance(actions, list):
            return {"ok": False, "error": "actions must be a non-empty list"}

        try:
            if isinstance(wait_between_ms, bool):
                raise TypeError("bool not allowed")
            clamped_wait_ms = max(0, min(int(wait_between_ms), 5000))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": f"Invalid wait_between_ms '{wait_between_ms}' (must be a non-negative integer; milliseconds)",
            }
        results: list[dict[str, Any]] = []
        for i, act in enumerate(actions):
            if cancel_token is not None and cancel_token.is_set():
                return {
                    "ok": False,
                    "error": "Caller cancelled batch",
                    "cancelled": True,
                    "step": i,
                    "completed": results,
                }
            act_type = str(act.get("action", "")).strip().lower() if isinstance(act, dict) else ""
            res: dict[str, Any] = {"action": act_type, "step": i}
            try:
                res.update(self._run_batch_action(act, i, act_type, cancel_token))
            except Exception as exc:
                res.update({"ok": False, "error": f"Exception at step {i} ({act_type}): {exc}"})
            results.append(res)
            if not res.get("ok"):
                return {
                    "ok": False,
                    "error": res.get("error") or f"Action '{act_type}' failed",
                    "step": i,
                    "completed": results,
                }
            if dialog := res.get("dialog"):
                # 弹窗后整批勿重试。
                remaining = len(actions) - i - 1
                if remaining == 0:
                    return {"ok": True, "steps_executed": len(results), "details": results}
                return {
                    "ok": False,
                    "error": (
                        f"Step {i} ({act_type}) was performed and opened a JavaScript {dialog.get('type')} dialog, "
                        f"so the remaining {remaining} action(s) were not run. Respond with browser_dialog, "
                        "then run the remaining actions."
                    ),
                    "step": i,
                    "completed": results,
                }
            if clamped_wait_ms > 0 and i < len(actions) - 1:
                _pause(clamped_wait_ms / 1000.0, cancel_token)

        self._operations.wait_for_page_stable(timeout_s=1.5)
        return {"ok": True, "steps_executed": len(results), "details": results}

    def _run_batch_action(
        self,
        act: object,
        step: int,
        act_type: str,
        cancel_token: threading.Event | None,
    ) -> dict[str, Any]:
        if not isinstance(act, dict):
            return {"ok": False, "error": f"Action at index {step} must be a dict"}
        raw_ref = act.get("ref")
        ref = "" if raw_ref is None else str(raw_ref).strip()

        if act_type == "click":
            return self._operations.click_ref(ref, wait_stable=False)
        if act_type == "type":
            return self._operations.type_ref(ref, str(act.get("text", "")), wait_stable=False)
        if act_type == "press":
            key = act.get("key")
            if not isinstance(key, str) or not key.strip():
                return {"ok": False, "error": f"'press' action at step {step} requires a non-empty 'key' field"}
            return self._operations.press_key(key.strip())
        if act_type == "hover":
            return self._operations.hover_ref(ref)
        if act_type == "scroll":
            try:
                pixels = int(round(parse_numeric_unit(act.get("pixels"), 500, valid_units=("px",))))
            except ValueError:
                return {"ok": False, "error": f"Invalid scroll pixels '{act.get('pixels')}' at step {step}"}
            return self._operations.scroll_page(str(act.get("direction", "down")), pixels)
        if act_type == "wait":
            try:
                wait_s = parse_numeric_unit(act.get("seconds"), 1.0, valid_units=("s", "ms"))
            except ValueError:
                return {"ok": False, "error": f"Invalid wait duration '{act.get('seconds')}' at step {step}"}
            clamped_s = max(0.0, min(wait_s, 10.0))
            _pause(clamped_s, cancel_token)
            return {"ok": True, "waited": clamped_s}
        if act_type == "select":
            return self._run_batch_select(act, step, ref)
        return {"ok": False, "error": f"Unknown action type '{act_type}' at step {step}"}

    def _run_batch_select(self, act: dict[str, Any], step: int, ref: str) -> dict[str, Any]:
        raw_val = act.get("value")
        val = str(raw_val) if raw_val is not None and not isinstance(raw_val, bool) else None
        raw_label = act.get("label")
        label = raw_label if isinstance(raw_label, str) and raw_label.strip() else None
        raw_idx = act.get("index")
        try:
            idx = int(raw_idx) if raw_idx is not None else None
        except (TypeError, ValueError):
            return {"ok": False, "error": f"Invalid select index '{raw_idx}' at step {step}"}
        if val is None and label is None and idx is None:
            return {"ok": False, "error": f"'select' action at step {step} requires one of 'value', 'label', 'index'"}
        sel_res = self._operations.select_ref(ref, value=val, label=label, index=idx)
        if not sel_res.get("success"):
            return {"ok": False, "error": sel_res.get("error", "Select failed")}
        return {"ok": True, "selected": sel_res.get("selected") or sel_res.get("text") or ref}


def _pause(seconds: float, cancel_token: threading.Event | None) -> None:
    """可被取消令牌提前唤醒的等待。"""
    if cancel_token is not None:
        cancel_token.wait(seconds)
    else:
        time.sleep(seconds)
