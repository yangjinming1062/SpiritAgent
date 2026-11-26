from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.tooling import tool_error
from spirit_agent_runner.features.browser.helpers import _truncate_snapshot
from spirit_agent_runner.features.browser.ownership import current_manager
from spirit_agent_runner.features.browser.session import allow_private_urls
from spirit_agent_runner.features.browser.supervisor import CDPSupervisor
from spirit_agent_runner.infrastructure.redact import SECRET_PREFIX_RE
from spirit_agent_runner.infrastructure.url_safety import (
    check_website_access,
    parse_browser_url,
    validate_browser_address,
)

NO_SUPERVISOR_MSG = "No browser session active. Call browser_navigate first."


def no_supervisor() -> dict[str, Any]:
    return {"success": False, "error": NO_SUPERVISOR_MSG}


def camofox_unsupported(tool_name: str) -> dict[str, Any]:
    return tool_error(f"{tool_name} is not supported with the Camofox backend.", success=False)


def _blocked(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


def _website_policy_error(url: str) -> dict[str, Any] | None:
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
    """工具请求的 URL 预检，不约束浏览器内部连接。"""
    if url == "about:blank":
        return url, None
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(unquote(url)):
        return url, _blocked(
            "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs."
        )
    try:
        target, normalized = parse_browser_url(url)
    except ValueError as exc:
        return url, _blocked(f"Blocked: {exc}")
    if (policy_error := _website_policy_error(normalized)) is not None:
        return url, policy_error
    try:
        validate_browser_address(target, allow_private=allow_private_urls() if allow_private is None else allow_private)
    except (OSError, UnicodeError):
        host = target.raw_host.decode("ascii")
        return url, _blocked(f"Cannot open URL: host {host!r} could not be resolved (DNS lookup failed)")
    except ValueError as exc:
        return url, _blocked(f"Blocked: {exc}")
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
def browser_session(*, create: bool = False) -> Iterator[CDPSupervisor | None]:
    """持有会话至工具返回；create 时按当前配置连接或启动，无会话由调用方返回错误。"""
    manager = current_manager()
    with manager.session(create=create) as info:
        supervisor = info.supervisor if info is not None else None
        if create:
            supervisor = manager.ensure_supervisor()
        yield supervisor
