import json
from contextlib import contextmanager
from urllib.parse import unquote

from utils import (
    SECRET_PREFIX_RE,
    check_website_access,
    is_always_blocked_url,
    is_safe_url,
    normalize_url_for_request,
)

from ...registry import tool_error
from ..session import _allow_private_urls, _last_session_key, touch_session
from ..supervisor import SUPERVISOR_REGISTRY

NO_SUPERVISOR_MSG = "No browser session active. Call browser_navigate first."


def no_supervisor() -> str:
    return json.dumps({"success": False, "error": NO_SUPERVISOR_MSG}, ensure_ascii=False)


def camofox_unsupported(tool_name: str) -> str:
    return tool_error(f"{tool_name} is not supported with the Camofox backend.", success=False)


def guard_browser_url(url: str, *, allow_private: bool | None = None) -> tuple[str | None, str | None]:
    """对即将送往浏览器的 URL 做 ``browser_navigate`` 同款 SSRF / 站点策略把关。

    返回 ``(normalized_url, error_json)``; ``normalized_url`` 为 ``None`` 时表示已被阻断,
    调用方应原样把 ``error_json`` 回给模型, **不要**再做任何网络动作。
    ``allow_private`` 显式传 ``True`` 用于本地浏览器场景；默认跟随 ``_allow_private_urls()`` 全局开关。
    """
    if not url or url == "about:blank":
        return url or "about:blank", None
    decoded = unquote(url)
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(decoded):
        return None, json.dumps(
            {
                "success": False,
                "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
            },
            ensure_ascii=False,
        )
    normalized = normalize_url_for_request(url)
    if is_always_blocked_url(normalized):
        return None, json.dumps(
            {"success": False, "error": "Blocked: URL targets a cloud metadata endpoint"},
            ensure_ascii=False,
        )
    effective_allow = allow_private if allow_private is not None else _allow_private_urls()
    if not effective_allow and not is_safe_url(normalized):
        return None, json.dumps(
            {"success": False, "error": "Blocked: URL targets a private or internal address"},
            ensure_ascii=False,
        )
    blocked = check_website_access(normalized)
    if blocked:
        return None, json.dumps(
            {
                "success": False,
                "error": blocked.message,
                "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source},
            },
            ensure_ascii=False,
        )
    return normalized, None


@contextmanager
def browser_session(task_id: str | None):
    """解析 session key → 取主管 → touch session → yield (supervisor, key)。

    若无活动主管，yield ``(None, session_key)``，调用方负责返回错误。
    """
    key = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(key)
    if supervisor is not None:
        touch_session(key)
    yield supervisor, key
