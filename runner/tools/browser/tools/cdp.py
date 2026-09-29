import json
import logging
from typing import Any

from ...registry import registry, tool_error
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_CDP_SCHEMA
from ..session import _get_cdp_override, touch_session
from ..supervisor import SUPERVISOR_REGISTRY
from ._common import NO_SUPERVISOR_MSG, camofox_unsupported, ensure_supervisor

logger = logging.getLogger(__name__)

_CDP_TIMEOUT_MIN = 1.0
_CDP_TIMEOUT_MAX = 300.0
_CDP_TIMEOUT_DEFAULT = 30.0

# 拒绝的方法：关闭浏览器或主管自身会话、一次性清空全部站点数据、绕过权限提示或弹窗管理、
# 改写下载目录（会破坏 browser_download，也可把下载写到任意目录），以及 Fetch 拦截
# （本工具不回传事件，主管也不处理 requestPaused，被拦截的请求会一直挂起）。
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
) -> str:
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

    session_key = task_id or "default"
    supervisor = SUPERVISOR_REGISTRY.get(session_key)
    if supervisor is None:
        if not _get_cdp_override():
            return tool_error(NO_SUPERVISOR_MSG)
        try:
            supervisor = ensure_supervisor(session_key)
        except Exception as exc:
            return tool_error(f"Failed to connect to CDP endpoint: {exc}")

    touch_session(session_key)
    call_params = params or {}
    safe_timeout = max(_CDP_TIMEOUT_MIN, min(float(timeout), _CDP_TIMEOUT_MAX))
    clamped = safe_timeout != float(timeout)

    session_id: str | None = None
    if target_id:
        _, attached = supervisor.get_attached_targets()
        session_id = attached.get(target_id, {}).get("session_id")
        if not session_id:
            attach_res = supervisor.attach_target(target_id)
            if not attach_res.get("ok"):
                return tool_error(f"Failed to attach to target {target_id}: {attach_res.get('error')}", method=method)
            session_id = attach_res["result"].get("sessionId")

    res = supervisor.send_cdp(method, call_params, timeout=safe_timeout, session_id=session_id)
    if not res.get("ok"):
        return tool_error(res.get("error", "CDP call failed"), method=method, target_id=target_id)
    return json.dumps(
        {
            "success": True,
            "method": method,
            "target_id": target_id,
            "result": res.get("result", {}),
            "timeout_clamped": clamped,
        },
        ensure_ascii=False,
    )


registry.register_tool("browser_cdp", check_fn=check_browser_native_requirements, schema=BROWSER_CDP_SCHEMA)(
    lambda args, **kw: browser_cdp(
        method=args.get("method", ""),
        params=args.get("params"),
        target_id=args.get("target_id"),
        timeout=args.get("timeout", _CDP_TIMEOUT_DEFAULT),
        task_id=kw.get("task_id"),
    ),
)
