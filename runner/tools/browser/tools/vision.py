import json
import logging
import uuid
from typing import Any

from utils import get_spiritagent_home

from ...registry import registry
from ..camofox import camofox_vision, is_camofox_mode
from ..check import check_browser_native_requirements
from ..helpers import screenshot_multimodal_result
from ..schemas import BROWSER_VISION_SCHEMA
from ._common import browser_session, no_supervisor

logger = logging.getLogger(__name__)


def browser_vision(annotate: bool = False, task_id: str | None = None) -> dict[str, Any] | str:
    """截图当前页面并把截图直接附到主对话上下文。"""
    if is_camofox_mode():
        return camofox_vision(annotate=annotate, task_id=task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        screenshots_dir = get_spiritagent_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        shot_res = supervisor.screenshot(path=screenshot_path, annotate=annotate)
        if not shot_res.get("ok"):
            return json.dumps({"success": False, "error": shot_res.get("error", "Failed to capture screenshot")})

        annotation_context = shot_res.get("annotation_context", "")
        if annotate:
            try:
                snap_res = supervisor.snapshot_axtree(interactive_only=True)
                snapshot_text = snap_res.get("snapshot", "") if snap_res.get("ok") else ""
                if not annotation_context:
                    annotation_context = (
                        f"\n\nAccessibility tree (element refs for interaction):\n{snapshot_text[:3000]}"
                    )
                else:
                    annotation_context = f"{annotation_context}\n\nAccessibility tree (element refs for interaction):\n{snapshot_text[:3000]}"
            except Exception as exc:
                logger.debug("Failed to obtain snapshot for vision annotation: %s", exc)

        return screenshot_multimodal_result(screenshot_path, annotation_context)


registry.register_tool("browser_vision", check_fn=check_browser_native_requirements, schema=BROWSER_VISION_SCHEMA)(
    lambda args, **kw: browser_vision(annotate=args.get("annotate", False), task_id=kw.get("task_id")),
)
