import json
from typing import Any

from ...registry import registry
from ..camofox import (
    camofox_click,
    camofox_press,
    camofox_scroll,
    camofox_type,
    is_camofox_mode,
)
from ..check import check_browser_native_requirements
from ..engine import select_option_with_eval
from ..schemas import (
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
from ._common import browser_session, camofox_unsupported, no_supervisor


def browser_click(ref: str, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_click(ref, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.click_ref(ref)
        if res.get("ok"):
            return json.dumps({"success": True, "clicked": ref})
        return json.dumps({"success": False, "error": res.get("error", f"Failed to click {ref}")})


def browser_type(ref: str, text: str, task_id: str | None = None) -> str:
    """聚焦并清空输入框，然后输入文本。"""
    if is_camofox_mode():
        return camofox_type(ref, text, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.type_ref(ref, text)
        if res.get("ok"):
            return json.dumps({"success": True, "typed": text, "element": ref})
        return json.dumps({"success": False, "error": res.get("error", f"Failed to type into {ref}")})


def browser_scroll(direction: str = "down", task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_scroll(direction, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.scroll_page(direction=direction)
        if res.get("ok"):
            return json.dumps({"success": True, "scrolled": direction})
        return json.dumps({"success": False, "error": res.get("error", "scroll failed")})


def browser_press(key: str, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_press(key, task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.press_key(key)
        if res.get("ok"):
            return json.dumps({"success": True, "pressed": key})
        return json.dumps({"success": False, "error": res.get("error", f"Failed to press key {key}")})


def browser_hover(ref: str, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_hover")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.hover_ref(ref)
        if res.get("ok"):
            return json.dumps({"success": True, "hovered": ref})
        return json.dumps({"success": False, "error": res.get("error", f"Failed to hover {ref}")})


def browser_drag(from_ref: str, to_ref: str, hold_key: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_drag")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.drag_refs(from_ref, to_ref, hold_key=hold_key)
        if res.get("ok"):
            return json.dumps({"success": True, "dragged": f"{from_ref} -> {to_ref}"})
        return json.dumps({"success": False, "error": res.get("error", f"Failed to drag from {from_ref} to {to_ref}")})


def browser_select(
    ref: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
    open_delay_s: float = 0.5,
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_select")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        def _eval(expr: str):
            return supervisor.evaluate_runtime(expr)

        res = select_option_with_eval(_eval, ref, value=value, label=label, index=index, open_delay_s=open_delay_s)
        return json.dumps(res, ensure_ascii=False)


def browser_wait_for(
    selector: str | None = None,
    text: str | None = None,
    timeout_s: float = 10.0,
    return_snapshot: bool = True,
    task_id: str | None = None,
    cancel_token: Any = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_wait_for")

    if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
        return json.dumps({"success": False, "error": "Caller cancelled before wait_for", "cancelled": True})
    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.wait_for(selector=selector, text=text, timeout_s=timeout_s, cancel_token=cancel_token)
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "wait_for timed out")})

        response: dict[str, Any] = {"success": True, "matched": res.get("matched"), "value": res.get("value")}
        if return_snapshot:
            snap_res = supervisor.snapshot_axtree(interactive_only=True)
            if snap_res.get("ok"):
                response["snapshot"] = snap_res.get("snapshot", "")
                response["element_count"] = snap_res.get("element_count", 0)

        return json.dumps(response, ensure_ascii=False)


def browser_find(query: str, ref_only: bool = True, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_find")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()
        res = supervisor.find_by_text(query, ref_only=ref_only)
        if res.get("ok"):
            matches = res.get("matches", [])
            return json.dumps({"success": True, "matches": matches, "count": len(matches)})
        return json.dumps({"success": False, "error": res.get("error", f"find failed for query {query!r}")})


registry.register_tool("browser_click", check_fn=check_browser_native_requirements, schema=BROWSER_CLICK_SCHEMA)(
    lambda args, **kw: browser_click(ref=args.get("ref", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_type", check_fn=check_browser_native_requirements, schema=BROWSER_TYPE_SCHEMA)(
    lambda args, **kw: browser_type(ref=args.get("ref", ""), text=args.get("text", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_scroll", check_fn=check_browser_native_requirements, schema=BROWSER_SCROLL_SCHEMA)(
    lambda args, **kw: browser_scroll(direction=args.get("direction", "down"), task_id=kw.get("task_id")),
)

registry.register_tool("browser_press", check_fn=check_browser_native_requirements, schema=BROWSER_PRESS_SCHEMA)(
    lambda args, **kw: browser_press(key=args.get("key", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_hover", check_fn=check_browser_native_requirements, schema=BROWSER_HOVER_SCHEMA)(
    lambda args, **kw: browser_hover(ref=args.get("ref", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_drag", check_fn=check_browser_native_requirements, schema=BROWSER_DRAG_SCHEMA)(
    lambda args, **kw: browser_drag(
        from_ref=args.get("from_ref", ""),
        to_ref=args.get("to_ref", ""),
        hold_key=args.get("hold_key"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool("browser_select", check_fn=check_browser_native_requirements, schema=BROWSER_SELECT_SCHEMA)(
    lambda args, **kw: browser_select(
        ref=args.get("ref", ""),
        value=args.get("value"),
        label=args.get("label"),
        index=args.get("index"),
        open_delay_s=args.get("open_delay_s", 0.5),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool("browser_wait_for", check_fn=check_browser_native_requirements, schema=BROWSER_WAIT_FOR_SCHEMA)(
    lambda args, **kw: browser_wait_for(
        selector=args.get("selector"),
        text=args.get("text"),
        timeout_s=args.get("timeout_s", 10.0),
        return_snapshot=args.get("return_snapshot", True),
        task_id=kw.get("task_id"),
        cancel_token=kw.get("cancel_token"),
    ),
)

registry.register_tool("browser_find", check_fn=check_browser_native_requirements, schema=BROWSER_FIND_SCHEMA)(
    lambda args, **kw: browser_find(
        query=args.get("query", ""),
        ref_only=args.get("ref_only", True),
        task_id=kw.get("task_id"),
    ),
)
