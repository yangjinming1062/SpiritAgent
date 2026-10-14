"""仅解析账户归属的资产引用，不读取业务状态。"""

import json
import re

from infrastructure.assets.asset_store import normalize_asset_reference
from infrastructure.assets.paths import parse_companion_asset_path

_ASSET_TOKEN = re.compile(
    r"(?<![\w/-])(?:https?://[^\s<>\"'()\[\]{}]+)?(?:/api/companion/asset/|companion-assets/)[^\s<>\"'()\[\]{},;]+",
)


def asset_paths(value: object, user_id: int) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(asset_paths(item, user_id) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(asset_paths(item, user_id) for item in value))
    if not isinstance(value, str):
        return set()
    result: set[str] = set()
    candidates = [value, *(match.group(0).rstrip(".!?，。；！") for match in _ASSET_TOKEN.finditer(value))]
    for candidate in candidates:
        path = normalize_asset_reference(candidate)
        parsed = parse_companion_asset_path(path)
        if parsed is not None and parsed[0] == user_id:
            result.add(path)
    if value.lstrip().startswith(("{", "[", '"')):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            if decoded != value:
                result |= asset_paths(decoded, user_id)
    return result
