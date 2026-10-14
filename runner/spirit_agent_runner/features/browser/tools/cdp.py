import logging
from typing import Any

from spirit_agent_runner.contracts.config import cfg_get, load_config
from spirit_agent_runner.contracts.tooling import ToolDefinition, tool_error
from spirit_agent_runner.features.browser.camofox import is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_cdp_requirements
from spirit_agent_runner.features.browser.schemas import BROWSER_CDP_SCHEMA
from spirit_agent_runner.features.browser.tools._common import NO_SUPERVISOR_MSG, browser_session, camofox_unsupported

logger = logging.getLogger(__name__)

_CDP_TIMEOUT_MIN = 1.0
_CDP_TIMEOUT_MAX = 300.0
_CDP_TIMEOUT_DEFAULT = 30.0

# 拒：关浏览器/会话、清空站点数据、绕过权限/弹窗、改下载目录、Fetch 拦截。
_BLOCKED_CDP_METHODS = frozenset(
    {
        "Browser.close",
        "Page.close",
        "Target.closeTarget",
        "Target.detachFromTarget",
        "Network.clearBrowserCookies",
        "Network.clearBrowserCache",
        "Storage.clearDataForOrigin",
        "Storage.clearCookies",
        "Storage.clearLocalStorage",
        "Storage.clearSessionStorage",
        "Storage.clearIndexedDB",
        "Storage.clearCacheStorage",
        "Storage.clearInterestGroups",
        "Storage.clearSharedStorage",
        "Storage.clearStorageBuckets",
        "Storage.resetSharedStorageBudget",
        "Page.handleJavaScriptDialog",
        "Browser.grantPermissions",
        "Browser.setPermission",
        "Browser.setDownloadBehavior",
        "Page.setDownloadBehavior",
        "Fetch.enable",
        "Fetch.disable",
    },
)


def browser_cdp(
    method: str,
    params: dict[str, Any] | None = None,
    target_id: str | None = None,
    timeout: float = _CDP_TIMEOUT_DEFAULT,
    task_id: str | None = None,
) -> dict[str, Any]:
    """转发一条原生 CDP 命令；统一经由主管调度。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_cdp")
    if not isinstance(method, str) or not method:
        return tool_error("'method' is required")

    if method in _BLOCKED_CDP_METHODS:
        logger.warning("browser_cdp blocked method %s (target=%s)", method, target_id)
        return tool_error(
            f"CDP method {method!r} is not allowed through browser_cdp. "
            "Use the dedicated browser tool if one covers this (e.g. browser_tab_close, browser_dialog, "
            "browser_cookies_clear).",
        )
    logger.info("browser_cdp %s (target=%s)", method, target_id)

    has_override = bool(str(cfg_get(load_config(), "browser", "cdp_url", default="")).strip())
    try:
        with browser_session(task_id, create=has_override) as (supervisor, _):
            if supervisor is None:
                return tool_error(NO_SUPERVISOR_MSG)
            call_params = params or {}
            safe_timeout = max(_CDP_TIMEOUT_MIN, min(float(timeout), _CDP_TIMEOUT_MAX))
            clamped = safe_timeout != float(timeout)

            session_id: str | None = None
            if target_id:
                _, attached = supervisor.pages.get_attached_targets()
                session_id = attached.get(target_id, {}).get("session_id")
                if not session_id:
                    attach_res = supervisor.pages.attach_target(target_id)
                    if not attach_res.get("ok"):
                        return tool_error(
                            f"Failed to attach to target {target_id}: {attach_res.get('error')}",
                            method=method,
                        )
                    session_id = attach_res["result"].get("sessionId")

            res = supervisor.send_cdp(method, call_params, timeout=safe_timeout, session_id=session_id)
            if not res.get("ok"):
                return tool_error(res.get("error", "CDP call failed"), method=method, target_id=target_id)
            return {
                "success": True,
                "method": method,
                "target_id": target_id,
                "result": res.get("result", {}),
                "timeout_clamped": clamped,
            }
    except Exception as exc:
        return tool_error(f"CDP request failed: {type(exc).__name__}: {exc}")


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_cdp",
            toolset_id="browser_automation",
            schema=BROWSER_CDP_SCHEMA,
            handler=lambda context, args: browser_cdp(
                method=args.get("method", ""),
                params=args.get("params"),
                target_id=args.get("target_id"),
                timeout=args.get("timeout", _CDP_TIMEOUT_DEFAULT),
                task_id=context.task_id,
            ),
            check_fn=check_browser_cdp_requirements,
            is_async=False,
        ),
    ]
