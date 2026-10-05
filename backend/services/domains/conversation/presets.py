from dataclasses import dataclass

from services.infrastructure.llm import ReasoningEffort

COMPANION_PRESET_ID = "companion"


@dataclass(frozen=True)
class InferenceDefaults:
    temperature: float
    context_compression_threshold: float
    reasoning_effort: ReasoningEffort


@dataclass(frozen=True)
class SystemPresetMeta:
    id: str
    name: str
    description: str
    icon_key: str
    inference_defaults: InferenceDefaults


SYSTEM_PRESET_CATALOG: dict[str, SystemPresetMeta] = {
    "companion": SystemPresetMeta(
        id="companion",
        name="陪伴",
        description="默认伴侣预设：完整 persona + 着装 + 工具教学 + 长程记忆。",
        icon_key="preset_companion",
        inference_defaults=InferenceDefaults(0.7, 0.7, "low"),
    ),
    "copywriter": SystemPresetMeta(
        id="copywriter",
        name="文案秘书",
        description="起草、润色与整理文案、邮件和会议记录，保留原意，贴合受众与场合。",
        icon_key="preset_copywriter",
        inference_defaults=InferenceDefaults(0.8, 0.75, "medium"),
    ),
    "language_teacher": SystemPresetMeta(
        id="language_teacher",
        name="语言老师",
        description="按水平与目标讲解、纠错和练习，提供忠实自然的翻译。",
        icon_key="preset_language_teacher",
        inference_defaults=InferenceDefaults(0.4, 0.75, "medium"),
    ),
}


def resolve_preset_meta(preset_id: str | None) -> SystemPresetMeta:
    if preset_id not in SYSTEM_PRESET_CATALOG:
        raise ValueError("Unknown system preset")
    return SYSTEM_PRESET_CATALOG[preset_id]
