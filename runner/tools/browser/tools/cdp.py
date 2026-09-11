import json
import logging
import time
from pathlib import Path
from typing import Any

from utils import get_spiritagent_home

from ...registry import registry, tool_error
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_CDP_SCHEMA
from ..session import _get_cdp_override, _last_session_key, touch_session
from ..supervisor import SUPERVISOR_REGISTRY

logger = logging.getLogger(__name__)

CDP_DOCS_URL = "https://chromedevtools.github.io/devtools-protocol/"
_CDP_TIMEOUT_MIN = 1.0
_CDP_TIMEOUT_MAX = 300.0
_CDP_TIMEOUT_DEFAULT = 30.0

# 明确破坏性的 CDP 方法 — 模型无合法用例，与一行的硬删除/关闭/清空等价。
# 不在黑名单的方法仍可调，但每条都会写入全量审计日志。
_BLOCKED_CDP_METHODS = frozenset(
    {
        "Browser.close",  # 关整个浏览器进程，会断所有 tab
        "Target.closeTarget",  # 关任意 target，包括其他 page
        "Target.detachFromTarget",  # 与 detachFromTarget 一起会丢 control
        "Network.clearBrowserCookies",  # 一次性清空所有 cookie
        "Network.clearBrowserCache",  # 一次性清空整个 HTTP 缓存
        "Storage.clearDataForOrigin",  # 与全 origin 清 storage（cookies/localStorage/IndexedDB...）
        "Storage.clearCookies",  # 同上
        "Storage.clearLocalStorage",
        "Storage.clearSessionStorage",
        "Storage.clearIndexedDB",
        "Storage.clearCacheStorage",
        "Storage.clearInterestGroups",
        "Storage.clearSharedStorage",
        "Storage.clearStorageBuckets",
        "Storage.resetSharedStorageBudget",
        "Page.handleJavaScriptDialog",  # 直接接管 JS 对话框（accept/dismiss），绕过 GUI 用户
        "Page.close",  # 关当前 page，但 Browser.close 不存在时不致命
        "Emulation.setEmulatedVisionDeficiency",  # 视障模拟可能掩盖 UI 元素
        "Browser.resetPermissions",
        "Browser.grantPermissions",  # 直接授所有权限给 origin，跳过用户提示
    },
)

_CDP_AUDIT_LOG = get_spiritagent_home() / "cdp-audit.log"


def _audit_cdp_call(
    task_id: str,
    method: str,
    target_id: str | None,
    frame_id: str | None,
    *,
    blocked: bool,
    reason: str | None,
) -> None:
    """每条 browser_cdp 调用都留痕，包括被黑名单拒的。"""
    try:
        Path(_CDP_AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)
        with Path(_CDP_AUDIT_LOG).open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "task_id": task_id,
                        "method": method,
                        "target_id": target_id,
                        "frame_id": frame_id,
                        "blocked": blocked,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
    except Exception as e:
        logger.debug("cdp audit log failed: %s", e)


def browser_cdp(
    method: str,
    params: dict[str, Any] | None = None,
    target_id: str | None = None,
    frame_id: str | None = None,
    timeout: float = _CDP_TIMEOUT_DEFAULT,
    task_id: str | None = None,
) -> str:
    """转发一条原生 CDP 命令；统一经由主管调度。"""
    if not isinstance(method, str) or not method:
        return tool_error("'method' is required", cdp_docs=CDP_DOCS_URL)

    # 黑名单优先：拒绝明显破坏性调用，不允许通过任何参数绕过。
    if method in _BLOCKED_CDP_METHODS:
        _audit_cdp_call(task_id or "default", method, target_id, frame_id, blocked=True, reason="blacklisted")
        return tool_error(
            f"CDP method {method!r} is blocked for browser_cdp (destructive, no legitimate agent use). "
            f"Use a higher-level tool (e.g. browser_tab_close) if you need a scoped equivalent. "
            f"Audit: {_CDP_AUDIT_LOG}",
            cdp_docs=CDP_DOCS_URL,
        )

    # 通过黑名单检查的调用也留痕（含 method/params 摘要）——便于事后审计。
    _audit_cdp_call(task_id or "default", method, target_id, frame_id, blocked=False, reason=None)

    effective_task_id = _last_session_key(task_id or "default")
    supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)

    if supervisor is None:
        cdp_url = _get_cdp_override()
        if not cdp_url:
            return tool_error(
                "No CDP endpoint available. Call browser_navigate first, or set 'browser.cdp_url'.",
                cdp_docs=CDP_DOCS_URL,
            )
        try:
            supervisor = SUPERVISOR_REGISTRY.get_or_start(effective_task_id, cdp_url)
        except Exception as exc:
            return tool_error(f"Failed to connect to CDP endpoint: {exc}", cdp_docs=CDP_DOCS_URL)

    touch_session(effective_task_id)
    call_params = params or {}
    safe_timeout = max(_CDP_TIMEOUT_MIN, min(float(timeout), _CDP_TIMEOUT_MAX))
    clamped = safe_timeout != float(timeout)

    if frame_id:
        frame, child_sid = supervisor.get_frame_session(frame_id)
        if frame is None:
            return tool_error(
                f"frame_id {frame_id!r} not found. Call browser_snapshot to refresh.",
                cdp_docs=CDP_DOCS_URL,
            )
        if not child_sid:
            return tool_error(
                f"frame_id {frame_id!r} is not an out-of-process iframe (no session).",
                cdp_docs=CDP_DOCS_URL,
            )

        res = supervisor.send_cdp(method, call_params, timeout=safe_timeout, session_id=child_sid)
        if not res.get("ok"):
            return tool_error(res.get("error", "CDP call failed"), method=method, frame_id=frame_id)
        return json.dumps(
            {
                "success": True,
                "method": method,
                "frame_id": frame_id,
                "session_id": child_sid,
                "result": res.get("result", {}),
                "timeout_clamped": clamped,
            },
            ensure_ascii=False,
        )

    if target_id:
        _, attached = supervisor.get_attached_targets()
        session_id = attached.get(target_id, {}).get("session_id")
        if not session_id:
            attach_res = supervisor._attach_target(target_id)
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

    res = supervisor.send_cdp(method, call_params, timeout=safe_timeout)
    if not res.get("ok"):
        return tool_error(res.get("error", "CDP call failed"), method=method)
    return json.dumps(
        {"success": True, "method": method, "result": res.get("result", {}), "timeout_clamped": clamped},
        ensure_ascii=False,
    )


registry.register_tool("browser_cdp", check_fn=check_browser_native_requirements, schema=BROWSER_CDP_SCHEMA)(
    lambda args, **kw: browser_cdp(
        method=args.get("method", ""),
        params=args.get("params"),
        target_id=args.get("target_id"),
        frame_id=args.get("frame_id"),
        timeout=args.get("timeout", _CDP_TIMEOUT_DEFAULT),
        task_id=kw.get("task_id"),
    ),
)
