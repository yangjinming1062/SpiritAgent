from dataclasses import dataclass
from typing import Any

from components import safe_json_loads


@dataclass(frozen=True)
class ToolsetDef:
    names: tuple[str, ...]


# 工具集 id 的权威枚举见 docs/PROTOCOL.md §2.2；此处只登记 backend/memory 桶拥有的 id，
# 其余 id（browser_automation 等）由 Runner 侧目录在 get_tools 源头过滤。
# 无工具集归属、不受开关影响的 backend 工具：search_tools（元工具）
# video_generate / video_generate_status（UI 未开对应工具集开关）。
TOOLSET_CATALOG: dict[str, ToolsetDef] = {
    "memory": ToolsetDef(names=("memory_retain", "memory_recall", "memory_inspect", "moment_create", "diary_write")),
    "web_tools": ToolsetDef(names=("web_search", "web_extract")),
    "image_generation": ToolsetDef(names=("image_generate", "room_backdrop_update")),
    "messaging": ToolsetDef(names=("send_message_tool",)),
    "scheduled_tasks": ToolsetDef(names=("cronjob",)),
    "agent_delegation": ToolsetDef(names=("agent_delegate_tool",)),
}


def disabled_backend_tool_names(user_settings: dict[str, Any]) -> set[str]:
    """因所属工具集被禁用而要对 backend/memory 桶隐藏的工具名集合。

    ``toolsets.disabled`` 畸形时返回空集（fail-open）——与 Runner 侧 get_disabled_config_names
    对齐：宁可多暴露工具，也不能因一个坏值把整张工具表清空。
    """
    val = user_settings.get("toolsets.disabled")
    raw = val if isinstance(val, list) else safe_json_loads(str(val or ""), default=None)
    if not isinstance(raw, list):
        return set()
    disabled_ids = {str(i).strip() for i in raw if isinstance(i, str) and i.strip()}
    return {name for tid, def_ in TOOLSET_CATALOG.items() if tid in disabled_ids for name in def_.names}
