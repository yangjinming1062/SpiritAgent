"""正式资产裸路径的跨层校验；不执行文件系统访问。"""

import re

_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def valid_asset_relative_path(value: str) -> bool:
    return bool(value) and all(part not in {"", ".", ".."} and _SEGMENT.fullmatch(part) for part in value.split("/"))


def parse_companion_asset_path(storage_path: str | None) -> tuple[int, str] | None:
    if not storage_path or not storage_path.startswith("companion-assets/"):
        return None
    parts = storage_path.split("/", 2)
    if len(parts) != 3 or not valid_asset_relative_path(parts[2]):
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    return (user_id, parts[2]) if user_id > 0 and str(user_id) == parts[1] else None
