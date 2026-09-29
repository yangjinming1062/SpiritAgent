import json
import uuid
from typing import Any

from utils import get_spiritagent_dir

from ...registry import registry
from ..camofox import camofox_vision, is_camofox_mode
from ..check import check_browser_native_requirements
from ..helpers import screenshot_multimodal_result
from ..schemas import BROWSER_VISION_SCHEMA
from ._common import browser_session, compact_snapshot, no_supervisor


def browser_vision(annotate: bool = False, task_id: str | None = None) -> dict[str, Any] | str:
    """截图当前页面并把截图直接附到主对话上下文。"""
    if is_camofox_mode():
        return camofox_vision(annotate=annotate, task_id=task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        screenshots_dir = get_spiritagent_dir("cache/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        shot_res = supervisor.screenshot(path=screenshot_path, annotate=annotate)
        if not shot_res.get("ok"):
            return json.dumps({"success": False, "error": shot_res.get("error", "Failed to capture screenshot")})

        annotation_context = shot_res.get("annotation_context", "")
        if annotate:
            snapshot = compact_snapshot(supervisor).get("snapshot")
            if snapshot:
                annotation_context += f"\n\nAccessibility tree (element refs for interaction):\n{snapshot[:3000]}"

        return screenshot_multimodal_result(screenshot_path, annotation_context)


registry.register_tool("browser_vision", check_fn=check_browser_native_requirements, schema=BROWSER_VISION_SCHEMA)(
    lambda args, **kw: browser_vision(annotate=args.get("annotate", False), task_id=kw.get("task_id")),
)
