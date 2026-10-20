from dataclasses import dataclass

PRESETS = frozenset({"companion", "copywriter", "language_teacher", "general"})


@dataclass(frozen=True, slots=True)
class SkillScope:
    user_id: int
    system_preset_id: str

    @classmethod
    def parse(cls, raw: object) -> "SkillScope":
        if (
            not isinstance(raw, dict)
            or type(raw.get("user_id")) is not int
            or raw["user_id"] <= 0
            or not isinstance(raw.get("system_preset_id"), str)
            or raw.get("system_preset_id") not in PRESETS
        ):
            raise ValueError("Valid server-controlled skill scope is required")
        return cls(raw["user_id"], raw["system_preset_id"])
