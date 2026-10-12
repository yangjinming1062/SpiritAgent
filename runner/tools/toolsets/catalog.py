import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# id 权威枚举在 toolset-index.ts；本目录只做 id→工具映射。system_awareness 单列便于隐私场景整组关闭（见 README）。
TOOLSET_CATALOG: tuple[ToolsetDef, ...] = (
    ToolsetDef(id="browser_automation", prefixes=("browser_",)),
    ToolsetDef(
        id="file_operations",
        extra_tools=("read_file", "write_file", "patch", "list_directory", "search_files"),
    ),
    ToolsetDef(id="terminal", extra_tools=("terminal",)),
    ToolsetDef(id="code_execution", extra_tools=("execute_code",)),
    ToolsetDef(id="process_management", extra_tools=("process",)),
    ToolsetDef(id="skills_system", extra_tools=("skills_list", "skill_view", "skill_manage")),
    ToolsetDef(id="computer_use", extra_tools=("computer_use",)),
    ToolsetDef(id="media_analysis", extra_tools=("vision_analyze",)),
    ToolsetDef(
        id="system_awareness",
        extra_tools=(
            "system.get_idle_seconds",
            "system.is_screen_locked",
            "system.get_focused_app",
            "system.is_fullscreen",
            "system.snapshot",
            "system.get_power_state",
            "system.get_windows",
            "system.open_application",
            "system.get_work_area",
            "system.get_cursor_pos",
            "system.click_at",
        ),
    ),
)

# Backend 桶 id 由后端过滤，不映射 Runner 工具；列出只为不把它们误报为未知 id。
_BACKEND_TOOLSET_IDS = frozenset(
    {"memory", "web_tools", "image_generation", "messaging", "scheduled_tasks", "agent_delegation"},
)
_KNOWN_TOOLSET_IDS: frozenset[str] = frozenset(d.id for d in TOOLSET_CATALOG) | _BACKEND_TOOLSET_IDS


def excluded_tool_names(disabled_ids: set[str], available_tool_names: set[str]) -> set[str]:
    """计算被禁用工具集须隐藏的工具名；未知 id 打 WARNING。"""
    if unknown := disabled_ids - _KNOWN_TOOLSET_IDS:
        logger.warning(
            "toolsets.disabled contains %d unknown id(s) ignored by runner catalog: %s. Valid ids: %s. Users may think these toolsets are disabled while they remain enabled.",
            len(unknown),
            sorted(unknown),
            sorted(_KNOWN_TOOLSET_IDS),
        )

    disabled_prefixes: tuple[str, ...] = tuple(p for d in TOOLSET_CATALOG if d.id in disabled_ids for p in d.prefixes)
    disabled_extras: set[str] = {n for d in TOOLSET_CATALOG if d.id in disabled_ids for n in d.extra_tools}

    excluded: set[str] = set()
    for name in available_tool_names:
        if name in disabled_extras or any(name.startswith(p) for p in disabled_prefixes):
            excluded.add(name)

    return excluded
