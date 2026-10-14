import threading
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import (
    camofox_click,
    camofox_press,
    camofox_scroll,
    camofox_type,
    is_camofox_mode,
)
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements, check_browser_native_requirements
from spirit_agent_runner.features.browser.schemas import (
    BROWSER_CLICK_SCHEMA,
    BROWSER_DRAG_SCHEMA,
    BROWSER_FIND_SCHEMA,
    BROWSER_HOVER_SCHEMA,
    BROWSER_PRESS_SCHEMA,
    BROWSER_SCROLL_SCHEMA,
    BROWSER_SELECT_SCHEMA,
    BROWSER_TYPE_SCHEMA,
    BROWSER_WAIT_FOR_SCHEMA,
)
from spirit_agent_runner.features.browser.tools._common import (
    action_error,
    action_success,
    browser_session,
    camofox_unsupported,
    compact_snapshot,
    no_supervisor,
)


def browser_click(ref: str, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_click(ref, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.click_ref(ref)
        if res.get("ok"):
            return action_success({"success": True, "clicked": ref}, res)
        return action_error(supervisor, res.get("error", f"Failed to click {ref}"))


def browser_type(ref: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    """聚焦并清空输入框，然后输入文本。"""
    if is_camofox_mode():
        return camofox_type(ref, text, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.type_ref(ref, text)
        if res.get("ok"):
            return action_success({"success": True, "typed": text, "element": ref}, res)
        return action_error(supervisor, res.get("error", f"Failed to type into {ref}"))


def browser_scroll(direction: str = "down", task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_scroll(direction, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.scroll_page(direction=direction)
        if res.get("ok"):
            return {"success": True, "scrolled": direction}
        return action_error(supervisor, res.get("error", "scroll failed"))


def browser_press(key: str, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_press(key, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.press_key(key)
        if res.get("ok"):
            return action_success({"success": True, "pressed": key}, res)
        return action_error(supervisor, res.get("error", f"Failed to press key {key}"))


def browser_hover(ref: str, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_hover")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.hover_ref(ref)
        if res.get("ok"):
            return {"success": True, "hovered": ref}
        return action_error(supervisor, res.get("error", f"Failed to hover {ref}"))


def browser_drag(from_ref: str, to_ref: str, hold_key: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_drag")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.drag_refs(from_ref, to_ref, hold_key=hold_key)
        if res.get("ok"):
            return {"success": True, "dragged": f"{from_ref} -> {to_ref}"}
        return action_error(supervisor, res.get("error", f"Failed to drag from {from_ref} to {to_ref}"))


def browser_select(
    ref: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_select")
    selectors = [value is not None, label is not None, index is not None]
    if sum(selectors) != 1:
        return {"success": False, "error": "Provide exactly one of value, label, or index."}

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.select_ref(ref, value=value, label=label, index=index)
        return res


def browser_wait_for(
    selector: str | None = None,
    text: str | None = None,
    timeout_s: float = 10.0,
    return_snapshot: bool = True,
    task_id: str | None = None,
    cancel_token: threading.Event | None = None,
) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_wait_for")
    if not selector and not text:
        return {"success": False, "error": "At least one of selector or text must be provided."}

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.wait_for(selector=selector, text=text, timeout_s=timeout_s, cancel_token=cancel_token)
        if not res.get("ok"):
            return action_error(supervisor, res.get("error", "wait_for timed out"))

        response: dict[str, Any] = {"success": True, "matched": res.get("matched"), "value": res.get("value")}
        if return_snapshot:
            response.update(compact_snapshot(supervisor))
        return response


def browser_find(query: str, ref_only: bool = True, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_find")
    if not query.strip():
        return {"success": False, "error": "query must not be empty"}

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.find_by_text(query, ref_only=ref_only)
        if res.get("ok"):
            matches = res.get("matches", [])
            return {"success": True, "matches": matches, "count": len(matches)}
        return {"success": False, "error": res.get("error", f"find failed for query {query!r}")}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_click",
            toolset_id="browser_automation",
            schema=BROWSER_CLICK_SCHEMA,
            handler=lambda context, args: browser_click(ref=args.get("ref", ""), task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_type",
            toolset_id="browser_automation",
            schema=BROWSER_TYPE_SCHEMA,
            handler=lambda context, args: browser_type(
                ref=args.get("ref", ""),
                text=args.get("text", ""),
                task_id=context.task_id,
            ),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_scroll",
            toolset_id="browser_automation",
            schema=BROWSER_SCROLL_SCHEMA,
            handler=lambda context, args: browser_scroll(
                direction=args.get("direction", "down"),
                task_id=context.task_id,
            ),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_press",
            toolset_id="browser_automation",
            schema=BROWSER_PRESS_SCHEMA,
            handler=lambda context, args: browser_press(key=args.get("key", ""), task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_hover",
            toolset_id="browser_automation",
            schema=BROWSER_HOVER_SCHEMA,
            handler=lambda context, args: browser_hover(ref=args.get("ref", ""), task_id=context.task_id),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_drag",
            toolset_id="browser_automation",
            schema=BROWSER_DRAG_SCHEMA,
            handler=lambda context, args: browser_drag(
                from_ref=args.get("from_ref", ""),
                to_ref=args.get("to_ref", ""),
                hold_key=args.get("hold_key"),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_select",
            toolset_id="browser_automation",
            schema=BROWSER_SELECT_SCHEMA,
            handler=lambda context, args: browser_select(
                ref=args.get("ref", ""),
                value=args.get("value"),
                label=args.get("label"),
                index=args.get("index"),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_wait_for",
            toolset_id="browser_automation",
            schema=BROWSER_WAIT_FOR_SCHEMA,
            handler=lambda context, args: browser_wait_for(
                selector=args.get("selector"),
                text=args.get("text"),
                timeout_s=args.get("timeout_s", 10.0),
                return_snapshot=args.get("return_snapshot", True),
                task_id=context.task_id,
                cancel_token=context.cancel_event,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_find",
            toolset_id="browser_automation",
            schema=BROWSER_FIND_SCHEMA,
            handler=lambda context, args: browser_find(
                query=args.get("query", ""),
                ref_only=args.get("ref_only", True),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
    ]
