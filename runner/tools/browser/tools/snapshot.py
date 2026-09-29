import json

from ...registry import registry
from ..camofox import camofox_snapshot, is_camofox_mode
from ..check import check_browser_native_requirements
from ..helpers import _truncate_snapshot
from ..schemas import BROWSER_SNAPSHOT_SCHEMA
from ._common import action_error, browser_session, no_supervisor


def browser_snapshot(full: bool = False, task_id: str | None = None) -> str:
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
        return json.dumps(response, ensure_ascii=False)


registry.register_tool("browser_snapshot", check_fn=check_browser_native_requirements, schema=BROWSER_SNAPSHOT_SCHEMA)(
    lambda args, **kw: browser_snapshot(full=args.get("full", False), task_id=kw.get("task_id")),
)
