"""人设持久化 JSON 的共享读取契约。"""

ONBOARDING_PROFILE_STEP = "onboarding_profile_step"


def parse_persona_definition(raw: dict[str, str] | None) -> dict[str, str]:
    value = raw or {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key != ONBOARDING_PROFILE_STEP and isinstance(item, str)}


def visual_persona_definition(definition: dict[str, str]) -> dict[str, str]:
    """视觉策划只取性格来安排气质与表演，背景设定不成为外形约束。"""
    return {"personality": definition["personality"]} if definition.get("personality") else {}
