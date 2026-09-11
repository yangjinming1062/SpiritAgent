import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# 工具集 id 的权威枚举见 docs/PROTOCOL.md §2.2；本目录只做 id → runner 侧工具名/前缀的映射。
# 不可控的"侧信道"系统感知能力（焦点窗口 / 工作区 / 屏幕坐标 / 全屏 / 屏幕锁 / 空闲时长）单独归档为
# ``system_awareness``,  让隐私敏感场景可以一键关掉屏幕坐标与全屏探测而不影响别的 system.* 探测。
# PROTOCOL §2.2 提到的 Backend-only id（memory / web_tools / image_generation / messaging /
# scheduled_tasks / agent_delegation）由后端独立归档, 不在此 catalog。
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

_VALID_TOOLSET_IDS: frozenset[str] = frozenset(d.id for d in TOOLSET_CATALOG)


def excluded_tool_names(disabled_ids: set[str], available_tool_names: set[str]) -> set[str]:
    """计算因所属 toolset 被禁用而要从 LLM-facing schema 中隐藏的具体工具名集合。

    ``available_tool_names`` 一般来自 ``registry.get_all_tool_names()`` — 因为前缀展开需要用具体名字过滤,
    所以我们拿实际名字来比对而不是伪造合成条目。

    对不在 ``TOOLSET_CATALOG`` 里的 id 打 WARNING: 用户在 Desktop 设置里手抖拼错
    (例如 ``browser_aut0mation``) 或旧版本残留 id 都不会命中 catalog, 默认 ``excluded`` 空集会让
    用户以为关闭了但实际还在 — 这是最危险的静默失败模式。
    """
    unknown = disabled_ids - _VALID_TOOLSET_IDS
    if unknown:
        logger.warning(
            "toolsets.disabled contains %d unknown id(s) ignored by runner catalog: %s. Valid ids: %s. Users may think these toolsets are disabled while they remain enabled.",
            len(unknown),
            sorted(unknown),
            sorted(_VALID_TOOLSET_IDS),
        )

    disabled_prefixes: tuple[str, ...] = tuple(p for d in TOOLSET_CATALOG if d.id in disabled_ids for p in d.prefixes)
    disabled_extras: set[str] = {n for d in TOOLSET_CATALOG if d.id in disabled_ids for n in d.extra_tools}

    excluded: set[str] = set()
    for name in available_tool_names:
        if name in disabled_extras or any(name.startswith(p) for p in disabled_prefixes):
            excluded.add(name)

    return excluded
