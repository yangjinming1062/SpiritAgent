import json
import logging
from typing import Any

from utils import is_always_blocked_url, is_safe_url

from ...registry import registry
from ..camofox import camofox_back, camofox_navigate, is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import BROWSER_BACK_SCHEMA, BROWSER_NAVIGATE_SCHEMA
from ..session import _allow_private_urls, touch_session
from ._common import (
    browser_session,
    compact_snapshot,
    ensure_supervisor,
    guard_browser_url,
    no_supervisor,
    unsafe_url_error,
    website_policy_error,
)

logger = logging.getLogger(__name__)

BLOCKED_PATTERNS = [
    "access denied",
    "access to this page has been denied",
    "blocked",
    "bot detected",
    "verification required",
    "please verify",
    "are you a robot",
    "captcha",
    "cloudflare",
    "ddos protection",
    "checking your browser",
    "just a moment",
    "attention required",
]


def _reject_redirect(final_url: str, original_url: str, allow_private: bool) -> str | None:
    """跳转落点的站点策略与 SSRF 复核；命中即返回错误 JSON。请求此时已发出，只能阻止继续读取落点页面。"""
    if not final_url or final_url == original_url:
        return None
    if (policy_error := website_policy_error(final_url)) is not None:
        return policy_error
    if is_always_blocked_url(final_url):
        return json.dumps(
            {"success": False, "error": "Blocked: redirect landed on a cloud metadata endpoint"},
            ensure_ascii=False,
        )
    if not allow_private and not is_safe_url(final_url):
        return json.dumps(
            {"success": False, "error": f"{unsafe_url_error(final_url)} after redirect"},
            ensure_ascii=False,
        )
    return None


def browser_navigate(url: str, task_id: str | None = None) -> str:
    """导航到指定 URL 并返回 JSON 结果（含首屏快照、跳转后 SSRF 校验、bot 检测提示）。"""
    if not url.strip():
        return json.dumps({"success": False, "error": "url is required"}, ensure_ascii=False)
    allow_private = _allow_private_urls()
    url, url_err = guard_browser_url(url.strip(), allow_private=allow_private)
    if url_err is not None:
        return url_err

    if is_camofox_mode():
        return camofox_navigate(url, task_id)

    session_key = task_id or "default"
    try:
        supervisor = ensure_supervisor(session_key)
        touch_session(session_key)

        nav_res = supervisor.navigate(url)
        final_url = nav_res.get("url", url)
        title = nav_res.get("title", "")

        reject = _reject_redirect(final_url, url, allow_private)
        if reject is not None:
            supervisor.navigate("about:blank")
            return reject

        response: dict[str, Any] = {"success": True, "url": final_url, "title": title}
        title_lower = title.lower()
        if any(p in title_lower for p in BLOCKED_PATTERNS):
            response["bot_detection_warning"] = (
                f"Page title '{title}' suggests bot detection; the site may have blocked this request. "
                "Try slowing down between actions or reaching the page through another path; "
                "some sites cannot be automated."
            )
        response.update(compact_snapshot(supervisor))
        return json.dumps(response, ensure_ascii=False)

    except Exception as e:
        logger.warning("browser_navigate failed: %s: %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def browser_back(task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_back(task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.back()
        if res.get("ok"):
            return json.dumps({"success": True})
        return json.dumps({"success": False, "error": res.get("error", "back navigation failed")})


registry.register_tool("browser_navigate", check_fn=check_browser_native_requirements, schema=BROWSER_NAVIGATE_SCHEMA)(
    lambda args, **kw: browser_navigate(url=args.get("url", ""), task_id=kw.get("task_id")),
)

registry.register_tool("browser_back", check_fn=check_browser_native_requirements, schema=BROWSER_BACK_SCHEMA)(
    lambda args, **kw: browser_back(task_id=kw.get("task_id")),  # noqa: ARG005
)
