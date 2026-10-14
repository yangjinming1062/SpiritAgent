"""Browser tool assembly and lifecycle entry points."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from spirit_agent_runner.contracts.tooling import ToolContext, ToolDefinition
from spirit_agent_runner.features.browser.ownership import browser_scope
from spirit_agent_runner.features.browser.session import BrowserManager
from spirit_agent_runner.features.browser.tools import (
    batch,
    capture,
    cdp,
    cookies,
    dialog,
    emulation,
    inspection,
    interactions,
    navigation,
    snapshot,
    tabs,
    vision,
)


def _bind_handler(
    manager: BrowserManager,
    handler: Callable[[ToolContext, dict[str, Any]], Any],
) -> Callable[[ToolContext, dict[str, Any]], Any]:
    def invoke(context: ToolContext, args: dict[str, Any]) -> Any:
        with browser_scope(manager):
            return handler(context, args)

    return invoke


def get_tool_definitions(manager: BrowserManager) -> list[ToolDefinition]:
    definitions: list[ToolDefinition] = []
    for module in (
        batch,
        capture,
        cdp,
        cookies,
        dialog,
        emulation,
        inspection,
        interactions,
        navigation,
        snapshot,
        tabs,
        vision,
    ):
        for definition in module.get_tool_definitions():
            definitions.append(replace(definition, handler=_bind_handler(manager, definition.handler)))
    return definitions
