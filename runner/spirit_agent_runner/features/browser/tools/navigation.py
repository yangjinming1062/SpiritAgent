import logging
from typing import Any

from spirit_agent_runner.contracts.config import load_config
from spirit_agent_runner.contracts.tooling import ToolDefinition
from spirit_agent_runner.features.browser.camofox import camofox_back, camofox_navigate, is_camofox_mode
from spirit_agent_runner.features.browser.check import check_browser_native_requirements
from spirit_agent_runner.features.browser.schemas import BROWSER_BACK_SCHEMA, BROWSER_NAVIGATE_SCHEMA
from spirit_agent_runner.features.browser.session import allow_private_urls
from spirit_agent_runner.features.browser.tools._common import (
    browser_session,
    compact_snapshot,
    guard_browser_url,
    no_supervisor,
    unsafe_url_error,
    website_policy_error,
)
from spirit_agent_runner.infrastructure.url_safety import is_always_blocked_url, is_safe_url

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


def _reject_redirect(final_url: str, original_url: str, allow_private: bool) -> dict[str, Any] | None:
    """跳转落点的站点策略与 SSRF 复核；命中即返回错误对象。请求此时已发出，只能阻止继续读取落点页面。"""
    if not final_url or final_url == original_url:
        return None
    if (policy_error := website_policy_error(final_url)) is not None:
        return policy_error
    if is_always_blocked_url(final_url):
        return {"success": False, "error": "Blocked: redirect landed on a cloud metadata endpoint"}
    if not allow_private and not is_safe_url(final_url, config=load_config()):
        return {"success": False, "error": f"{unsafe_url_error(final_url)} after redirect"}
    return None


def browser_navigate(url: str, task_id: str | None = None) -> dict[str, Any]:
    """导航到指定 URL 并返回结果对象（含首屏快照、跳转后 SSRF 校验、bot 检测提示）。"""
    if not url.strip():
        return {"success": False, "error": "url is required"}
    allow_private = allow_private_urls()
    url, url_err = guard_browser_url(url.strip(), allow_private=allow_private)
    if url_err is not None:
        return url_err

    if is_camofox_mode():
        return camofox_navigate(url, task_id)

    try:
        with browser_session(task_id, create=True) as (supervisor, _):
            if supervisor is None:
                return no_supervisor()
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
            return response

    except Exception as e:
        logger.warning("browser_navigate failed: %s: %s", type(e).__name__, e)
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def browser_back(task_id: str | None = None) -> dict[str, Any]:
    if is_camofox_mode():
        return camofox_back(task_id)

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.back()
        if res.get("ok"):
            return {"success": True}
        return {"success": False, "error": res.get("error", "back navigation failed")}


def get_tool_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser_navigate",
            toolset_id="browser_automation",
            schema=BROWSER_NAVIGATE_SCHEMA,
            handler=lambda context, args: browser_navigate(url=args.get("url", ""), task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
        ToolDefinition(
            name="browser_back",
            toolset_id="browser_automation",
            schema=BROWSER_BACK_SCHEMA,
            handler=lambda context, _args: browser_back(task_id=context.task_id),
            check_fn=check_browser_native_requirements,
            is_async=False,
        ),
    ]
