from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import camofox_get_images, is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements, check_browser_native_requirements
from spirit_agent_runner.features.browser.schemas import BROWSER_CONSOLE_SCHEMA, BROWSER_GET_IMAGES_SCHEMA
from spirit_agent_runner.features.browser.tools._common import browser_session, camofox_unsupported, no_supervisor


def browser_console(clear: bool = False, expression: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_console")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        if expression:
            eval_res = supervisor.evaluate_runtime(expression)
            if eval_res.get("ok"):
                return {
                    "success": True,
                    "expression": expression,
                    "result": eval_res.get("result"),
                    "result_type": eval_res.get("result_type"),
                }
            return {"success": False, "expression": expression, "error": eval_res.get("error", "Evaluation failed")}

        msgs = supervisor.pages.console_messages(clear=clear)
        logs = [m for m in msgs if m.get("level") != "exception"]
        errors = [m for m in msgs if m.get("level") == "exception"]

        return {
            "success": True,
            "console_messages": logs,
            "js_errors": errors,
            "total_messages": len(logs),
            "total_errors": len(errors),
        }


def browser_get_images(task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_get_images(task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.get_images()
        if res.get("ok"):
            images = res.get("result", [])
            return {"success": True, "images": images, "count": len(images)}
        return {"success": False, "error": res.get("error", "Failed to retrieve images")}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_console",
            toolset_id="browser_automation",
            schema=BROWSER_CONSOLE_SCHEMA,
            handler=lambda context, args: browser_console(
                clear=args.get("clear", False),
                expression=args.get("expression"),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_get_images",
            toolset_id="browser_automation",
            schema=BROWSER_GET_IMAGES_SCHEMA,
            handler=lambda context, _args: browser_get_images(task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
    ]
