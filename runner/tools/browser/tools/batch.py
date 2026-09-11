import json
import logging
from typing import Any

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_BATCH_SCHEMA
from ._common import browser_session, camofox_unsupported, no_supervisor

logger = logging.getLogger(__name__)


def browser_batch(
    actions: list[dict[str, Any]],
    return_snapshot: bool = True,
    wait_between_ms: int = 100,
    task_id: str | None = None,
    cancel_token: Any = None,
) -> str:
    """在当前浏览器会话中连续批量执行一组原子动作。

    减少跨进程/云端多轮往返延迟，支持 click, type, press, hover, scroll, wait, select 等动作。
    """
    if is_camofox_mode():
        return camofox_unsupported("browser_batch")

    if not actions or not isinstance(actions, list):
        return json.dumps({"success": False, "error": "actions parameter must be a non-empty list of action objects"})

    if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
        return json.dumps({"success": False, "error": "Caller cancelled before batch", "cancelled": True})

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        batch_res = supervisor.execute_batch(actions, wait_between_ms=wait_between_ms, cancel_token=cancel_token)
        if not batch_res.get("ok"):
            return json.dumps(
                {
                    "success": False,
                    "error": batch_res.get("error", "Batch execution failed"),
                    "step": batch_res.get("step"),
                    "completed_steps": batch_res.get("completed", []),
                },
                ensure_ascii=False,
            )

        result: dict[str, Any] = {
            "success": True,
            "steps_executed": batch_res.get("steps_executed", len(actions)),
            "details": batch_res.get("details", []),
        }

        if return_snapshot:
            try:
                snap_res = supervisor.snapshot_axtree(interactive_only=True)
                if snap_res.get("ok"):
                    result["snapshot"] = snap_res.get("snapshot", "")
            except Exception as exc:
                logger.debug("Failed to get post-batch snapshot: %s", exc)

        return json.dumps(result, ensure_ascii=False)


registry.register_tool("browser_batch", check_fn=check_browser_native_requirements, schema=BROWSER_BATCH_SCHEMA)(
    lambda args, **kw: browser_batch(
        actions=args.get("actions", []),
        return_snapshot=args.get("return_snapshot", True),
        wait_between_ms=args.get("wait_between_ms", 100),
        task_id=kw.get("task_id"),
        cancel_token=kw.get("cancel_token"),
    ),
)
