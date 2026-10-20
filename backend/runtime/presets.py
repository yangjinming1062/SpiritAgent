from dataclasses import dataclass

COMPANION_PRESET_ID = "companion"
# 通用助手：只共用工作预设体、不追加角色头部，用于不需要特定系统角色的普通任务与对话。
GENERAL_PRESET_ID = "general"


@dataclass(frozen=True)
class SystemPresetMeta:
    id: str
    name: str
    description: str
    icon_key: str


SYSTEM_PRESET_CATALOG: dict[str, SystemPresetMeta] = {
    "companion": SystemPresetMeta(
        id="companion",
        name="陪伴",
        description="默认伴侣预设：完整 persona + 着装 + 工具教学 + 长程记忆。",
        icon_key="preset_companion",
    ),
    "copywriter": SystemPresetMeta(
        id="copywriter",
        name="文案秘书",
        description="起草、润色与整理文案、邮件和会议记录，保留原意，贴合受众与场合。",
        icon_key="preset_copywriter",
    ),
    "language_teacher": SystemPresetMeta(
        id="language_teacher",
        name="语言老师",
        description="按水平与目标讲解、纠错和练习，提供忠实自然的翻译。",
        icon_key="preset_language_teacher",
    ),
    "general": SystemPresetMeta(
        id="general",
        name="通用助手",
        description="不带角色设定的通用对话：保留工具调用、记忆与环境提示，只去掉具体角色与专业指引。",
        icon_key="preset_general",
    ),
}


def resolve_preset_meta(preset_id: str | None) -> SystemPresetMeta:
    if preset_id not in SYSTEM_PRESET_CATALOG:
        raise ValueError("Unknown system preset")
    return SYSTEM_PRESET_CATALOG[preset_id]
