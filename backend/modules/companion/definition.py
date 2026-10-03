"""人设持久化 JSON 的共享读取契约。"""

from components import safe_json_loads


def parse_persona_definition(raw: str | None) -> dict[str, str]:
    value = safe_json_loads(raw or "{}", default={})
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(item, str)}
