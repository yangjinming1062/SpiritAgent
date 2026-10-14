from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements
from spirit_agent_runner.features.browser.schemas import BROWSER_DIALOG_SCHEMA
from spirit_agent_runner.features.browser.tools._common import browser_session, camofox_unsupported, no_supervisor

_VALID_DIALOG_ACTIONS = frozenset({"accept", "dismiss"})


def browser_dialog(
    action: str,
    prompt_text: str | None = None,
    dialog_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """应答当前阻塞页面的 JS 弹窗（accept / dismiss）。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_dialog")

    if action not in _VALID_DIALOG_ACTIONS:
        return {"success": False, "error": f"action must be 'accept' or 'dismiss', got {action!r}"}

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.respond_to_dialog(action=action, prompt_text=prompt_text, dialog_id=dialog_id)
        if res.get("ok"):
            return {"success": True, "action": action, "dialog": res.get("dialog", {})}
        return {"success": False, "error": res.get("error", "unknown error")}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_dialog",
            toolset_id="browser_automation",
            schema=BROWSER_DIALOG_SCHEMA,
            handler=lambda context, args: browser_dialog(
                action=args.get("action", ""),
                prompt_text=args.get("prompt_text"),
                dialog_id=args.get("dialog_id"),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
    ]
