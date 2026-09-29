import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# 工具集 id 的权威枚举见 client/main/shared/lib/toolset-index.ts；本目录只做 Runner 侧 id → 工具名/前缀的映射。
# 不可控的"侧信道"系统感知能力（焦点窗口 / 工作区 / 屏幕坐标 / 全屏 / 屏幕锁 / 空闲时长）单独归档为
# ``system_awareness``,  让隐私敏感场景可以一键关掉屏幕坐标与全屏探测而不影响别的 system.* 探测。
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
    """计算 ``available_tool_names`` 中因所属工具集被禁用而须隐藏并拒绝派发的工具名（前缀须按具体名字展开）。

    未知 id（拼错或已移除）不会命中任何工具，用户会误以为已关闭，因此打 WARNING。
    """
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
