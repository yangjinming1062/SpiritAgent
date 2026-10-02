from typing import Any

# 工具集 id 权威枚举在 client/main/shared/lib/toolset-index.ts；此处只登记 backend/memory 桶 id，其余由 Runner get_tools 源头过滤。无归属且不受开关影响：search_tools、video_generate / video_generate_status。
_TOOLSET_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "memory": ("memory_retain", "memory_recall", "memory_inspect"),
    "posts": ("post_publish", "post_status"),
    "web_tools": ("web_search", "web_extract"),
    "image_generation": (
        "image_generate",
        "media_inspect",
        "image_regenerate",
        "scene_list",
        "scene_get",
        "scene_create",
        "scene_activate",
    ),
    "messaging": ("send_message_tool",),
    "scheduled_tasks": ("cronjob", "companion_wait"),
    "agent_delegation": ("agent_delegate_tool",),
}


def disabled_backend_tool_names(user_settings: dict[str, Any]) -> set[str]:
    """因所属工具集被禁用而要对 backend/memory 桶隐藏的工具名集合。toolsets.disabled 畸形时返回空集（fail-open）——与 Runner 侧对齐：宁可多暴露工具，也不能因一个坏值把整张工具表清空。"""
    raw = user_settings.get("toolsets.disabled")
    if not isinstance(raw, list):
        return set()
    disabled_ids = {i.strip() for i in raw if isinstance(i, str) and i.strip()}
    return {name for tid, names in _TOOLSET_TOOL_NAMES.items() if tid in disabled_ids for name in names}
