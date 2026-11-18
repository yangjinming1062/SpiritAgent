from infrastructure.text import safe_json_loads

# 只收录无持久副作用、重试安全的查询工具（含访问外部服务的 web_search/web_extract），其余工具一律视为可能有副作用；新增查询工具时对照
# tool_runtime/domains.py 的 DOMAIN_CATALOG 与 runner/tools/toolsets/catalog.py 同步更新。
_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "search_tools",
        "companion_wait",
        "memory_inspect",
        "memory_recall",
        "scene_list",
        "scene_get",
        "action_search",
        "action_inspect",
        "visual_search",
        "visual_inspect",
        "outfit_list",
        "life_inspect",
        "life_recall",
        "diary_recall",
        "video_generate_status",
        "read_file",
        "list_directory",
        "search_files",
        "skills_list",
        "skill_view",
        "web_search",
        "web_extract",
        "system.snapshot",
        "system.get_idle_seconds",
        "system.is_screen_locked",
        "system.get_focused_app",
        "system.is_fullscreen",
        "system.get_power_state",
        "system.get_windows",
        "system.get_work_area",
        "system.get_cursor_pos",
    }
)


_FILE_MUTATING_TOOL_NAMES = frozenset({"write_file", "patch"})


def tool_may_have_effects(tool_name: object) -> bool:
    return not isinstance(tool_name, str) or tool_name not in _READ_ONLY_TOOLS


def file_mutation_result_landed(tool_name: str, result: str) -> bool:
    """文件写入结果能证明写入确实落到磁盘时返回 True。"""
    if tool_name not in _FILE_MUTATING_TOOL_NAMES:
        return False
    data = safe_json_loads(result.strip())
    if not isinstance(data, dict) or data.get("error"):
        return False
    return ("bytes_written" in data) if tool_name == "write_file" else data.get("success") is True
