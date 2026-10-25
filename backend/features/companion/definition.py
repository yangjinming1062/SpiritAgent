"""人设持久化 JSON 的共享读取契约。"""


def parse_persona_definition(raw: dict[str, str] | None) -> dict[str, str]:
    value = raw or {}
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(item, str)}
