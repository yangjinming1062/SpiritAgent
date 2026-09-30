import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote, urlsplit

from utils import (
    SECRET_PREFIX_RE,
    check_website_access,
    is_always_blocked_url,
    is_safe_url,
    normalize_url_for_request,
)

from ...registry import tool_error
from ..engine import launch_chromium
from ..helpers import _truncate_snapshot
from ..profile_manager import resolve_profile_dir
from ..session import (
    _allow_private_urls,
    _get_cdp_override,
    _get_dialog_policy_config,
    get_or_create_session,
    touch_session,
)
from ..supervisor import SUPERVISOR_REGISTRY, CDPSupervisor

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


def no_supervisor() -> str:
    return json.dumps({"success": False, "error": NO_SUPERVISOR_MSG}, ensure_ascii=False)


def camofox_unsupported(tool_name: str) -> str:
    return tool_error(f"{tool_name} is not supported with the Camofox backend.", success=False)


def _blocked(error: str) -> str:
    return json.dumps({"success": False, "error": error}, ensure_ascii=False)


def website_policy_error(url: str) -> str | None:
    """站点黑名单命中时返回错误 JSON。"""
    blocked = check_website_access(url)
    if blocked is None:
        return None
    return json.dumps(
        {
            "success": False,
            "error": blocked.message,
            "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source},
        },
        ensure_ascii=False,
    )


def guard_browser_url(url: str, *, allow_private: bool | None = None) -> tuple[str, str | None]:
    """浏览器 URL 预检（凭据/协议/SSRF/站点策略）；返回 (url, error_json|None)。页面内跳转不经此。"""
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
    effective_allow = _allow_private_urls() if allow_private is None else allow_private
    if not effective_allow and not is_safe_url(normalized):
        return url, _blocked(unsafe_url_error(normalized))
    if (policy_error := website_policy_error(normalized)) is not None:
        return url, policy_error
    return normalized, None


def ensure_supervisor(session_key: str) -> CDPSupervisor:
    """返回会话的活动主管；没有时连接 ``browser.cdp_url`` 或启动本机 Chromium。"""
    supervisor = SUPERVISOR_REGISTRY.get(session_key)
    if supervisor is not None and supervisor.active:
        return supervisor

    session_info = get_or_create_session(session_key)
    # 失联时先清旧本机浏览器放 profile 锁；覆盖模式不拥有进程。
    if session_info.launch_handle is not None:
        session_info.launch_handle.terminate()
        session_info.launch_handle = None
    if not (cdp_url := _get_cdp_override()):
        session_info.launch_handle = launch_chromium(profile_dir=resolve_profile_dir(session_key))
        cdp_url = session_info.launch_handle.cdp_url

    policy, timeout_s = _get_dialog_policy_config()
    return SUPERVISOR_REGISTRY.get_or_start(
        session_key,
        cdp_url,
        launch_handle=session_info.launch_handle,
        auto_owned=session_info.launch_handle is not None,
        dialog_policy=policy,
        dialog_timeout_s=timeout_s,
    )


def pending_dialog_fields(supervisor: CDPSupervisor) -> dict[str, Any]:
    """JS 弹窗阻塞页面时依赖页面的动作与快照都会失败（错误文本已指向 browser_dialog）；附上结构化的未决弹窗。"""
    pending = supervisor.snapshot().pending_dialogs
    return {"pending_dialogs": [dialog.to_dict() for dialog in pending]} if pending else {}


def action_success(payload: dict[str, Any], res: dict[str, Any]) -> str:
    """动作成功的结果；动作本身打开了 JS 弹窗时附上弹窗，提示先应答而不是重复该动作。"""
    if dialog := res.get("dialog"):
        payload |= {
            "dialog": dialog,
            "hint": "The action was performed and opened a JavaScript dialog; respond to it with browser_dialog.",
        }
    return json.dumps(payload, ensure_ascii=False)


def action_error(supervisor: CDPSupervisor, error: str) -> str:
    return json.dumps({"success": False, "error": error, **pending_dialog_fields(supervisor)}, ensure_ascii=False)


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
def browser_session(task_id: str | None) -> Iterator[tuple[CDPSupervisor | None, str]]:
    """取会话主管并刷新活跃时间；无活动主管时 yield ``(None, key)``，由调用方返回错误。"""
    key = task_id or "default"
    supervisor = SUPERVISOR_REGISTRY.get(key)
    if supervisor is not None:
        touch_session(key)
    yield supervisor, key
