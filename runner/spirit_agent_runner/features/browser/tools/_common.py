import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote, urlsplit

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.tooling import tool_error
from spirit_agent_runner.features.browser.helpers import _truncate_snapshot
from spirit_agent_runner.features.browser.ownership import current_manager
from spirit_agent_runner.features.browser.session import allow_private_urls
from spirit_agent_runner.features.browser.supervisor import CDPSupervisor
from spirit_agent_runner.infrastructure.redact import SECRET_PREFIX_RE
from spirit_agent_runner.infrastructure.url_safety import (
    check_website_access,
    is_always_blocked_url,
    is_safe_url,
    normalize_url_for_request,
)

NO_SUPERVISOR_MSG = "No browser session active. Call browser_navigate first."
PRIVATE_URL_BLOCKED_MSG = (
    "Blocked: URL targets a private or internal address (intranet access is turned off in settings)"
)


def unsafe_url_error(url: str) -> str:
    """``is_safe_url`` 拒绝时的错误文案：域名解析失败与内网开关无关，须分开报告，避免误导用户去改设置。"""
    host = urlsplit(url).hostname or ""
    try:
        socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return f"Cannot open URL: host {host!r} could not be resolved (DNS lookup failed)"
    return PRIVATE_URL_BLOCKED_MSG


def no_supervisor() -> dict[str, Any]:
    return {"success": False, "error": NO_SUPERVISOR_MSG}


def camofox_unsupported(tool_name: str) -> dict[str, Any]:
    return tool_error(f"{tool_name} is not supported with the Camofox backend.", success=False)


def _blocked(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


def website_policy_error(url: str) -> dict[str, Any] | None:
    """站点黑名单命中时返回错误对象。"""
    blocked = check_website_access(url, config=load_config())
    if blocked is None:
        return None
    return {
        "success": False,
        "error": blocked.message,
        "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source},
    }


def guard_browser_url(url: str, *, allow_private: bool | None = None) -> tuple[str, dict[str, Any] | None]:
    """浏览器 URL 预检（凭据/协议/SSRF/站点策略）；返回 (url, error|None)。页面内跳转不经此。"""
    if url == "about:blank":
        return url, None
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(unquote(url)):
        return url, _blocked(
            "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
        )
    normalized = normalize_url_for_request(url)
    if urlsplit(normalized).scheme.lower() not in {"http", "https"}:
        return url, _blocked("Blocked: only http and https URLs can be opened")
    if is_always_blocked_url(normalized):
        return url, _blocked("Blocked: URL targets a cloud metadata endpoint")
    effective_allow = allow_private_urls() if allow_private is None else allow_private
    if not effective_allow and not is_safe_url(normalized, config=load_config()):
        return url, _blocked(unsafe_url_error(normalized))
    if (policy_error := website_policy_error(normalized)) is not None:
        return url, policy_error
    return normalized, None


def pending_dialog_fields(supervisor: CDPSupervisor) -> dict[str, Any]:
    """JS 弹窗阻塞页面时依赖页面的动作与快照都会失败（错误文本已指向 browser_dialog）；附上结构化的未决弹窗。"""
    pending = supervisor.snapshot().pending_dialogs
    return {"pending_dialogs": [dialog.to_dict() for dialog in pending]} if pending else {}


def action_success(payload: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    """动作成功的结果；动作本身打开了 JS 弹窗时附上弹窗，提示先应答而不是重复该动作。"""
    if dialog := res.get("dialog"):
        payload |= {
            "dialog": dialog,
            "hint": "The action was performed and opened a JavaScript dialog; respond to it with browser_dialog.",
        }
    return payload


def action_error(supervisor: CDPSupervisor, error: str) -> dict[str, Any]:
    return {"success": False, "error": error, **pending_dialog_fields(supervisor)}


def compact_snapshot(supervisor: CDPSupervisor) -> dict[str, Any]:
    """动作后附带的紧凑快照；失败时返回 ``snapshot_error``，不影响动作本身的结果。"""
    try:
        res = supervisor.snapshot_axtree(interactive_only=True)
    except Exception as exc:
        return {"snapshot_error": f"{type(exc).__name__}: {exc}"}
    if not res.get("ok"):
        return {"snapshot_error": res.get("error", "snapshot failed")}
    return {"snapshot": _truncate_snapshot(res.get("snapshot", "")), "element_count": res.get("element_count", 0)}


@contextmanager
def browser_session(task_id: str | None, *, create: bool = False) -> Iterator[tuple[CDPSupervisor | None, str]]:
    """持有会话至工具返回；create 时按当前配置连接或启动，无会话由调用方返回错误。"""
    key = task_id or "default"
    manager = current_manager()
    with manager.session(key, create=create) as info:
        supervisor = info.supervisor if info is not None else None
        if create:
            supervisor = manager.ensure_supervisor(key)
        yield supervisor, key
