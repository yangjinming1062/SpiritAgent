from dataclasses import dataclass

PRESETS = frozenset({"companion", "copywriter", "language_teacher", "general"})


@dataclass(frozen=True, slots=True)
class SkillScope:
    user_id: int
    system_preset_id: str
