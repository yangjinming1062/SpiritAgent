"""5 套内置系统提示词预设。预设体里的 ``{{BLOCK}}`` 由 ``prompt_blocks.substitute`` 严格解析。

预设体变更需要 backend 重启（与现有静态常量节奏一致）；运行时不做热更新。
"""

import logging

from components import resolve_prompt_text
from modules.system import PromptPreset
from prompts.chat import PRESET_BODY_AUTOMATION, PRESET_BODY_COMPANION, PRESET_BODY_WORK, PRESET_HEADER_TEXTS

from services.domains.conversation import SYSTEM_PRESET_CATALOG

logger = logging.getLogger(__name__)

AUTOMATION_PRESET = PromptPreset(
    id="automation",
    name="自动化任务",
    description="",
    icon_key="task",
    body=PRESET_BODY_AUTOMATION,
)
# 生活空间工具只服务陪伴会话：工作预设与自动化任务在回合装配层（build_turn_inputs）与
# search_tools 元工具同源过滤，压根不注入 schema，工具入口不再二次判定会话类型。
LIFE_SPACE_TOOL_NAMES = frozenset(
    {
        "send_message_tool",
        "companion_wait",
        "diary_write",
        "moment_create",
        "scene_list",
        "scene_get",
        "scene_create",
        "scene_activate",
        "action_play",
    },
)
AUTOMATION_EXCLUDED_TOOL_NAMES = LIFE_SPACE_TOOL_NAMES | frozenset(
    {
        "memory_inspect",
        "memory_recall",
        "memory_retain",
        "session_search",
        "cronjob",
        "skills_list",
        "skill_view",
        "skill_manage",
    },
)


def _build_body(preset: PromptPreset, language: str) -> str:
    header_dict = PRESET_HEADER_TEXTS.get(preset.id)
    if header_dict is None:
        return preset.body
    header = resolve_prompt_text(header_dict, language)
    return f"{header}\n\n{preset.body}"


def _preset_from_catalog(preset_id: str, body: str) -> PromptPreset:
    meta = SYSTEM_PRESET_CATALOG[preset_id]
    return PromptPreset(id=meta.id, name=meta.name, description=meta.description, icon_key=meta.icon_key, body=body)


BUILTIN_PRESETS: dict[str, PromptPreset] = {
    "companion": _preset_from_catalog("companion", PRESET_BODY_COMPANION),
    "developer": _preset_from_catalog("developer", PRESET_BODY_WORK),
    "product_manager": _preset_from_catalog("product_manager", PRESET_BODY_WORK),
    "copywriter": _preset_from_catalog("copywriter", PRESET_BODY_WORK),
    "language_teacher": _preset_from_catalog("language_teacher", PRESET_BODY_WORK),
}


def resolve_preset(preset_id: str | None) -> PromptPreset:
    if preset_id not in BUILTIN_PRESETS:
        raise ValueError("Unknown system preset")
    return BUILTIN_PRESETS[preset_id]
