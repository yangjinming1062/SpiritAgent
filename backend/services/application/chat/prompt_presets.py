"""内置系统提示词预设体，按会话的 ``system_preset_id`` 选取；预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks`` 渲染。"""

from components import resolve_prompt_text
from prompts.chat import PRESET_BODY_AUTOMATION, PRESET_BODY_COMPANION, PRESET_BODY_WORK, PRESET_HEADER_TEXTS

from services.domains.conversation import COMPANION_PRESET_ID, resolve_preset_meta

# 生活空间工具只服务陪伴会话：其余预设与自动化任务由回合装配、search_tools 与派发层共用同一排除集合，不注入 schema 也不派发。
LIFE_SPACE_TOOL_NAMES = frozenset(
    {
        "send_message_tool",
        "companion_wait",
        "post_publish",
        "post_status",
        "scene_list",
        "scene_get",
        "scene_create",
        "scene_activate",
        "action_play",
        "action_search",
        "action_design",
        "action_inspect",
    },
)
AUTOMATION_EXCLUDED_TOOL_NAMES = LIFE_SPACE_TOOL_NAMES | frozenset(
    {
        "memory_inspect",
        "memory_recall",
        "memory_retain",
        "cronjob",
        "skills_list",
        "skill_view",
        "skill_manage",
    },
)


def preset_body(preset_id: str, language: str) -> str:
    """陪伴与自动化各用独立预设体；职业预设在共享工作预设体前加各自的双语头部。"""
    # 自动化会话以字面量 "automation" 识别：is_automation 与 system_preset_id == "automation" 由数据库约束保证等价。
    if preset_id == "automation":
        return PRESET_BODY_AUTOMATION
    if preset_id == COMPANION_PRESET_ID:
        return PRESET_BODY_COMPANION
    # 仅作校验：不在预设目录中的 id 抛 ValueError，不回落为工作预设体。
    resolve_preset_meta(preset_id)
    return f"{resolve_prompt_text(PRESET_HEADER_TEXTS[preset_id], language)}\n\n{PRESET_BODY_WORK}"


def preset_excluded_tool_names(preset_id: str) -> frozenset[str]:
    """预设不绑定的工具；回合装配与执行层共用同一集合，生活空间工具只对陪伴预设开放。"""
    if preset_id == "automation":
        return AUTOMATION_EXCLUDED_TOOL_NAMES
    return frozenset() if preset_id == COMPANION_PRESET_ID else LIFE_SPACE_TOOL_NAMES
