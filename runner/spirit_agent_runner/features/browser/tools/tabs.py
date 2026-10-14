from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements
from spirit_agent_runner.features.browser.schemas import (
    BROWSER_TAB_CLOSE_SCHEMA,
    BROWSER_TAB_LIST_SCHEMA,
    BROWSER_TAB_NEW_SCHEMA,
    BROWSER_TAB_SWITCH_SCHEMA,
)
from spirit_agent_runner.features.browser.tools._common import (
    browser_session,
    camofox_unsupported,
    guard_browser_url,
    no_supervisor,
)


def browser_tab_new(url: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_new")

    target_url, url_err = guard_browser_url((url or "").strip() or "about:blank")
    if url_err is not None:
        return url_err

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        create_res = supervisor.send_cdp("Target.createTarget", {"url": target_url})
        if not create_res.get("ok"):
            return {"success": False, "error": create_res.get("error", "Target.createTarget failed")}

        target_id = create_res["result"].get("targetId")
        if not target_id:
            return {"success": False, "error": "No targetId returned from Target.createTarget"}

        attach_res = supervisor.pages.attach_target(target_id)
        if not attach_res.get("ok"):
            return {"success": False, "error": attach_res.get("error", "Failed to attach to new tab")}

        session_id = attach_res["result"].get("sessionId")
        if not session_id:
            return {"success": False, "error": f"No session returned for new tab {target_id}"}
        activated = supervisor.pages.activate_tab_session(session_id)
        if not activated.get("ok"):
            return {"success": False, "error": activated.get("error", "Failed to activate the new tab")}
        return {"success": True, "tab_id": target_id, "url": target_url}


def browser_tab_switch(tab_id: str, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_switch")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        _, attached = supervisor.pages.get_attached_targets()
        session_id = attached.get(tab_id, {}).get("session_id")

        if not session_id:
            attach_res = supervisor.pages.attach_target(tab_id)
            if not attach_res.get("ok"):
                return {"success": False, "error": f"Failed to attach to tab {tab_id}: {attach_res.get('error')}"}
            session_id = attach_res["result"].get("sessionId")
            if not session_id:
                return {"success": False, "error": f"No session returned for tab {tab_id}"}

        activated = supervisor.pages.activate_tab_session(session_id)
        if not activated.get("ok"):
            return {"success": False, "error": activated.get("error", "Failed to switch tabs")}
        return {"success": True, "active_tab_id": tab_id}


def browser_tab_close(tab_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_close")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.pages.close_tab(tab_id)
        if res.get("ok"):
            return {"success": True, "closed_tab_id": res.get("tab_id")}
        return {"success": False, "error": res.get("error", "Failed to close tab")}


def browser_tab_list(task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_unsupported("browser_tab_list")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.pages.list_tabs()
        if not res.get("ok"):
            return {"success": False, "error": res.get("error", "Target.getTargets failed")}

        targets = res["result"].get("targetInfos", [])
        active_sid, attached = supervisor.pages.get_attached_targets()

        tabs = []
        for t in targets:
            if t.get("type") == "page":
                tid = t.get("targetId")
                sid = attached.get(tid, {}).get("session_id")
                is_active = (sid == active_sid) if active_sid else False
                tabs.append(
                    {
                        "tab_id": tid,
                        "url": t.get("url", ""),
                        "title": t.get("title", ""),
                        "is_active": is_active,
                    },
                )

        return {"success": True, "tabs": tabs, "count": len(tabs)}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_tab_new",
            toolset_id="browser_automation",
            schema=BROWSER_TAB_NEW_SCHEMA,
            handler=lambda context, args: browser_tab_new(url=args.get("url"), task_id=context.task_id),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_tab_switch",
            toolset_id="browser_automation",
            schema=BROWSER_TAB_SWITCH_SCHEMA,
            handler=lambda context, args: browser_tab_switch(tab_id=args.get("tab_id", ""), task_id=context.task_id),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_tab_close",
            toolset_id="browser_automation",
            schema=BROWSER_TAB_CLOSE_SCHEMA,
            handler=lambda context, args: browser_tab_close(tab_id=args.get("tab_id"), task_id=context.task_id),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_tab_list",
            toolset_id="browser_automation",
            schema=BROWSER_TAB_LIST_SCHEMA,
            handler=lambda context, _args: browser_tab_list(task_id=context.task_id),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
    ]
