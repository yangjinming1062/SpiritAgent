import json

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_DIALOG_SCHEMA
from ._common import browser_session, camofox_unsupported, no_supervisor

_VALID_DIALOG_ACTIONS = frozenset({"accept", "dismiss"})


def browser_dialog(
    action: str,
    prompt_text: str | None = None,
    dialog_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """应答当前阻塞页面的 JS 弹窗（accept / dismiss）。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_dialog")

    if action not in _VALID_DIALOG_ACTIONS:
        return json.dumps(
            {"success": False, "error": f"action must be 'accept' or 'dismiss', got {action!r}"},
            ensure_ascii=False,
        )

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.respond_to_dialog(action=action, prompt_text=prompt_text, dialog_id=dialog_id)
        if res.get("ok"):
            return json.dumps({"success": True, "action": action, "dialog": res.get("dialog", {})}, ensure_ascii=False)
        return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)


registry.register_tool("browser_dialog", check_fn=check_browser_native_requirements, schema=BROWSER_DIALOG_SCHEMA)(
    lambda args, **kw: browser_dialog(
        action=args.get("action", ""),
        prompt_text=args.get("prompt_text"),
        dialog_id=args.get("dialog_id"),
        task_id=kw.get("task_id"),
    ),
)
