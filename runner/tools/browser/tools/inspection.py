import json
import logging

from ...registry import registry
from ..camofox import camofox_get_images, is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_CONSOLE_SCHEMA, BROWSER_GET_IMAGES_SCHEMA
from ._common import browser_session, no_supervisor

logger = logging.getLogger(__name__)


def browser_console(clear: bool = False, expression: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        # Camofox 不支持 console 捕获（避免假成功让模型以为页面无错）。
        # 显式 success=false + unsupported_by_backend 让模型立刻看到限制。
        return json.dumps(
            {
                "success": False,
                "unsupported_by_backend": "camofox",
                "note": "Console log capture is not available with the Camofox backend. Use browser_cdp('Runtime.evaluate', ...) to inspect state, or switch to the CDP backend.",
            },
            ensure_ascii=False,
        )

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        if expression:
            eval_res = supervisor.evaluate_runtime(expression)
            if eval_res.get("ok"):
                return json.dumps(
                    {
                        "success": True,
                        "expression": expression,
                        "result": eval_res.get("result"),
                        "result_type": eval_res.get("result_type"),
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {"success": False, "expression": expression, "error": eval_res.get("error", "Evaluation failed")},
                ensure_ascii=False,
            )

        msgs = supervisor.console_messages(clear=clear)
        logs = [m for m in msgs if m.get("level") != "exception"]
        errors = [m for m in msgs if m.get("level") == "exception"]

        return json.dumps(
            {
                "success": True,
                "console_messages": logs,
                "js_errors": errors,
                "total_messages": len(logs),
                "total_errors": len(errors),
            },
            ensure_ascii=False,
        )


def browser_get_images(task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_get_images(task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.get_images()
        if res.get("ok"):
            images = res.get("result", [])
            return json.dumps({"success": True, "images": images, "count": len(images)}, ensure_ascii=False)
        return json.dumps(
            {"success": False, "error": res.get("error", "Failed to retrieve images")},
            ensure_ascii=False,
        )


registry.register_tool("browser_console", check_fn=check_browser_native_requirements, schema=BROWSER_CONSOLE_SCHEMA)(
    lambda args, **kw: browser_console(
        clear=args.get("clear", False),
        expression=args.get("expression"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_get_images",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_GET_IMAGES_SCHEMA,
)(
    lambda args, **kw: browser_get_images(task_id=kw.get("task_id")),  # noqa: ARG005
)
