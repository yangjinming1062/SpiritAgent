"""内置系统提示词预设体，按会话的 ``system_preset_id`` 选取；预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks`` 渲染。"""

from components import resolve_prompt_text
from prompts.chat import PRESET_BODY_AUTOMATION, PRESET_BODY_COMPANION, PRESET_BODY_WORK, PRESET_HEADER_TEXTS

from services.domains.companion import is_work_preset

# is_automation 与 system_preset_id == "automation" 由数据库约束保证等价；陪伴与职业预设由 conversation 领域的预设目录校验。
PRESET_BODIES: dict[str, str] = {
    "companion": PRESET_BODY_COMPANION,
    "developer": PRESET_BODY_WORK,
    "product_manager": PRESET_BODY_WORK,
    "copywriter": PRESET_BODY_WORK,
    "language_teacher": PRESET_BODY_WORK,
    "automation": PRESET_BODY_AUTOMATION,
}
# 生活空间工具只服务陪伴会话：工作预设与自动化任务在回合装配层与 search_tools 同源过滤，不注入 schema，工具入口不再二次判定。
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
    """职业预设在共享工作预设体前加各自的双语头部。"""
    header = PRESET_HEADER_TEXTS.get(preset_id)
    body = PRESET_BODIES[preset_id]
    return f"{resolve_prompt_text(header, language)}\n\n{body}" if header else body


def preset_excluded_tool_names(preset_id: str) -> frozenset[str]:
    """预设不绑定的工具；回合装配与执行层共用同一集合。"""
    if preset_id == "automation":
        return AUTOMATION_EXCLUDED_TOOL_NAMES
    return LIFE_SPACE_TOOL_NAMES if is_work_preset(preset_id) else frozenset()
