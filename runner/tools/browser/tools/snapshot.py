import json
import logging

from ...registry import registry
from ..camofox import camofox_snapshot, is_camofox_mode
from ..check import check_browser_native_requirements
from ..helpers import (
    SNAPSHOT_SUMMARIZE_THRESHOLD,
    _extract_relevant_content,
    _truncate_snapshot,
)
from ..schemas import BROWSER_SNAPSHOT_SCHEMA
from ._common import browser_session, no_supervisor

logger = logging.getLogger(__name__)


def browser_snapshot(full: bool = False, task_id: str | None = None, user_task: str | None = None) -> str:
    """获取当前页面可访问性树快照（紧凑或完整）；超长时结合 user_task 调用 LLM 抽取，否则按行截断。"""
    if is_camofox_mode():
        return camofox_snapshot(full, task_id, user_task)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.snapshot_axtree(full=full, interactive_only=not full)
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "Failed to get snapshot")})

        snapshot_text = res.get("snapshot", "")
        element_count = res.get("element_count", 0)

        if len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD and user_task:
            snapshot_text = _extract_relevant_content(snapshot_text, user_task)
        elif len(snapshot_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            snapshot_text = _truncate_snapshot(snapshot_text)

        response = {"success": True, "snapshot": snapshot_text, "element_count": element_count}

        try:
            sv_snap = supervisor.snapshot()
            if sv_snap.active:
                response.update(sv_snap.to_dict())
        except Exception as exc:
            logger.debug("supervisor snapshot merge failed: %s", exc)

        return json.dumps(response, ensure_ascii=False)


registry.register_tool("browser_snapshot", check_fn=check_browser_native_requirements, schema=BROWSER_SNAPSHOT_SCHEMA)(
    lambda args, **kw: browser_snapshot(
        full=args.get("full", False),
        task_id=kw.get("task_id"),
        user_task=kw.get("user_task"),
    ),
)
