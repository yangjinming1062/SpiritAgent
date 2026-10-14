"""社交来源登录凭据的结构校验，不请求平台或记录凭据内容。"""

import json
import re

_X_COOKIE_NAMES = ("auth_token", "ct0")


def _unique_json_object(entries: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for name, item in entries:
        if name in value:
            raise ValueError("JSON Cookie 对象含重复字段")
        value[name] = item
    return value


def _json_cookie_entries(raw: str) -> list[tuple[str, object]]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError):
        return []
    if isinstance(value, dict) and "cookies" in value:
        value = value["cookies"]
    if isinstance(value, dict):
        return [(name, value[name]) for name in _X_COOKIE_NAMES if name in value]
    if not isinstance(value, list):
        return []
    entries: list[tuple[str, object]] = []
    for cookie in value:
        if not isinstance(cookie, dict) or not isinstance(cookie.get("name"), str):
            return []
        name = cookie["name"]
        if name not in _X_COOKIE_NAMES:
            continue
        if "domain" in cookie:
            domain = cookie["domain"]
            if not isinstance(domain, str):
                return []
            domain = domain.lower().removeprefix(".")
            if not any(domain == root or domain.endswith(f".{root}") for root in ("x.com", "twitter.com")):
                continue
        entries.append((name, cookie.get("value")))
    return entries


def _header_cookie_entries(raw: str) -> list[tuple[str, object]]:
    if any(ord(character) < 32 or ord(character) > 126 for character in raw):
        return []
    raw = raw.strip()
    if raw[:7].lower() == "cookie:":
        raw = raw[7:].strip()
    entries: list[tuple[str, object]] = []
    for fragment in raw.split(";"):
        fragment = fragment.strip()
        if not fragment:
            continue
        name, separator, value = fragment.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[!#$%&'*+\-.^_`|~A-Za-z0-9]+", name):
            return []
        if name in _X_COOKIE_NAMES:
            value = value.strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            entries.append((name, value))
    return entries


def parse_x_search_cookies(raw: str) -> dict[str, str]:
    """读取 Cookie 请求头或浏览器 JSON 导出，仅保留登录与 CSRF Cookie。"""
    value = raw.strip()
    if not value or len(raw) > 30000:
        return {}
    entries = _json_cookie_entries(value) if value[0] in "[{" else _header_cookie_entries(raw)
    selected: dict[str, str] = {}
    for name, value in entries:
        if (
            not isinstance(value, str)
            or not value
            or any(ord(character) < 33 or ord(character) > 126 or character in ';,"\\' for character in value)
            or name in selected
            and selected[name] != value
        ):
            return {}
        selected[name] = value
    return {name: selected[name] for name in _X_COOKIE_NAMES} if len(selected) == 2 else {}
