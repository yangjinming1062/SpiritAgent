import threading
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements
from spirit_agent_runner.features.browser.schemas import BROWSER_BATCH_SCHEMA
from spirit_agent_runner.features.browser.tools._common import (
    browser_session,
    camofox_unsupported,
    compact_snapshot,
    no_supervisor,
    pending_dialog_fields,
)

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
) -> dict[str, Any]:
    """按序执行一组页面动作，首个失败即停止并返回已执行步骤。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_batch")

    if not actions or not isinstance(actions, list):
        return {"success": False, "error": "actions parameter must be a non-empty list of action objects"}
    if validation_error := _validate_batch_actions(actions):
        return {"success": False, "error": validation_error}

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        batch_res = supervisor.batch.execute_batch(actions, wait_between_ms=wait_between_ms, cancel_token=cancel_token)
        if not batch_res.get("ok"):
            return {
                "success": False,
                "error": batch_res.get("error", "Batch execution failed"),
                "step": batch_res.get("step"),
                "completed_steps": batch_res.get("completed", []),
                **pending_dialog_fields(supervisor),
            }

        result: dict[str, Any] = {
            "success": True,
            "steps_executed": batch_res.get("steps_executed", len(actions)),
            "details": batch_res.get("details", []),
        }
        if return_snapshot:
            result.update(compact_snapshot(supervisor))
        return result


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_batch",
            toolset_id="browser_automation",
            schema=BROWSER_BATCH_SCHEMA,
            handler=lambda context, args: browser_batch(
                actions=args.get("actions", []),
                return_snapshot=args.get("return_snapshot", True),
                wait_between_ms=args.get("wait_between_ms", 100),
                task_id=context.task_id,
                cancel_token=context.cancel_event,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
    ]
