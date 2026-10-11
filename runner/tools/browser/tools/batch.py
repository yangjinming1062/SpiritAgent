import json
import threading
from typing import Any

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_cdp_requirements
from ..schemas import BROWSER_BATCH_SCHEMA
from ._common import browser_session, camofox_unsupported, compact_snapshot, no_supervisor, pending_dialog_fields

_BATCH_ACTIONS = frozenset({"click", "type", "press", "hover", "scroll", "wait", "select"})


def _validate_batch_actions(actions: list[dict[str, Any]]) -> str | None:
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            return f"Action at index {index} must be an object"
        kind = action.get("action")
        if not isinstance(kind, str) or kind not in _BATCH_ACTIONS:
            return f"Unknown action type {kind!r} at step {index}; use {', '.join(sorted(_BATCH_ACTIONS))}"
        required: tuple[str, ...] = ()
        if kind in {"click", "hover"}:
            required = ("ref",)
        elif kind == "type":
            required = ("ref", "text")
        elif kind == "press":
            required = ("key",)
        elif kind == "scroll":
            required = ("direction",)
        elif kind == "select":
            selectors = [name for name in ("value", "label", "index") if name in action]
            if len(selectors) != 1:
                return f"select action at step {index} requires exactly one of value, label or index"
            required = ("ref",)
        missing = [name for name in required if name not in action]
        if missing:
            return f"{kind} action at step {index} is missing required field(s): {', '.join(missing)}"
    return None


def browser_batch(
    actions: list[dict[str, Any]],
    return_snapshot: bool = True,
    wait_between_ms: int = 100,
    task_id: str | None = None,
    cancel_token: threading.Event | None = None,
) -> str:
    """按序执行一组页面动作，首个失败即停止并返回已执行步骤。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_batch")

    if not actions or not isinstance(actions, list):
        return json.dumps({"success": False, "error": "actions parameter must be a non-empty list of action objects"})
    if validation_error := _validate_batch_actions(actions):
        return json.dumps({"success": False, "error": validation_error}, ensure_ascii=False)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        batch_res = supervisor.execute_batch(actions, wait_between_ms=wait_between_ms, cancel_token=cancel_token)
        if not batch_res.get("ok"):
            return json.dumps(
                {
                    "success": False,
                    "error": batch_res.get("error", "Batch execution failed"),
                    "step": batch_res.get("step"),
                    "completed_steps": batch_res.get("completed", []),
                    **pending_dialog_fields(supervisor),
                },
                ensure_ascii=False,
            )

        result: dict[str, Any] = {
            "success": True,
            "steps_executed": batch_res.get("steps_executed", len(actions)),
            "details": batch_res.get("details", []),
        }
        if return_snapshot:
            result.update(compact_snapshot(supervisor))
        return json.dumps(result, ensure_ascii=False)


registry.register_tool("browser_batch", check_fn=check_browser_cdp_requirements, schema=BROWSER_BATCH_SCHEMA)(
    lambda args, **kw: browser_batch(
        actions=args.get("actions", []),
        return_snapshot=args.get("return_snapshot", True),
        wait_between_ms=args.get("wait_between_ms", 100),
        task_id=kw.get("task_id"),
        cancel_token=kw.get("cancel_token"),
    ),
)
