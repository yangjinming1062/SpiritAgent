from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import camofox_snapshot, is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_native_requirements
from spirit_agent_runner.features.browser.helpers import _truncate_snapshot
from spirit_agent_runner.features.browser.schemas import BROWSER_SNAPSHOT_SCHEMA
from spirit_agent_runner.features.browser.tools._common import action_error, browser_session, no_supervisor


def browser_snapshot(full: bool = False, task_id: str | None = None) -> dict[str, Any]:
    """获取当前页面可访问性树快照（紧凑或完整），超长按行截断，并附未决弹窗与 frame 概况。"""
    if is_camofox_mode():
        return camofox_snapshot(task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.snapshot_axtree(interactive_only=not full)
        if not res.get("ok"):
            return action_error(supervisor, res.get("error", "Failed to get snapshot"))

        response = {
            "success": True,
            "snapshot": _truncate_snapshot(res.get("snapshot", "")),
            "element_count": res.get("element_count", 0),
        }

        sv_snap = supervisor.snapshot()
        if sv_snap.active:
            response.update(sv_snap.to_dict())
        return response


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_snapshot",
            toolset_id="browser_automation",
            schema=BROWSER_SNAPSHOT_SCHEMA,
            handler=lambda context, args: browser_snapshot(full=args.get("full", False), task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
    ]
