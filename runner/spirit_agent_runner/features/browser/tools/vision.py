import uuid
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import camofox_vision, is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_native_requirements
from spirit_agent_runner.features.browser.helpers import screenshot_multimodal_result
from spirit_agent_runner.features.browser.schemas import BROWSER_VISION_SCHEMA
from spirit_agent_runner.features.browser.tools._common import browser_session, compact_snapshot, no_supervisor
from spirit_agent_runner.infrastructure.constants import get_spiritagent_dir


def browser_vision(annotate: bool = False, task_id: str | None = None) -> dict[str, Any]:
    """截图当前页面并把截图直接附到主对话上下文。"""
    if is_camofox_mode():
        return camofox_vision(annotate=annotate, task_id=task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        screenshots_dir = get_spiritagent_dir("cache/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"browser_screenshot_{uuid.uuid4().hex[:8]}.png")

        shot_res = supervisor.capture.screenshot(path=screenshot_path, annotate=annotate)
        if not shot_res.get("ok"):
            return {"success": False, "error": shot_res.get("error", "Failed to capture screenshot")}

        annotation_context = shot_res.get("annotation_context", "")
        if annotate:
            snapshot = compact_snapshot(supervisor).get("snapshot")
            if snapshot:
                annotation_context += f"\n\nAccessibility tree (element refs for interaction):\n{snapshot[:3000]}"

        return screenshot_multimodal_result(screenshot_path, annotation_context)


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_vision",
            toolset_id="browser_automation",
            schema=BROWSER_VISION_SCHEMA,
            handler=lambda context, args: browser_vision(annotate=args.get("annotate", False), task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
    ]
