import json
import logging
from typing import Any
from urllib.parse import unquote

from utils import (
    SECRET_PREFIX_RE,
    check_website_access,
    is_always_blocked_url,
    is_safe_url,
    normalize_url_for_request,
)

from ...registry import registry
from ..camofox import camofox_back, camofox_navigate, is_camofox_mode
from ..check import check_browser_native_requirements
from ..engine import launch_chromium
from ..helpers import SNAPSHOT_SUMMARIZE_THRESHOLD, _truncate_snapshot
from ..profile_manager import resolve_profile_dir
from ..schemas import BROWSER_BACK_SCHEMA, BROWSER_NAVIGATE_SCHEMA
from ..session import (
    _allow_private_urls,
    _get_cdp_override,
    _get_dialog_policy_config,
    _last_active_session_key,
    _navigation_session_key,
    get_or_create_session,
    touch_session,
)
from ..supervisor import SUPERVISOR_REGISTRY, CDPSupervisor
from ._common import browser_session, no_supervisor

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


def _ensure_supervisor(session_key: str) -> CDPSupervisor:
    supervisor = SUPERVISOR_REGISTRY.get(session_key)
    if supervisor is not None and supervisor._active:
        return supervisor

    session_info = get_or_create_session(session_key)
    cdp_url = _get_cdp_override()
    auto_owned = False

    if not cdp_url:
        profile_dir = resolve_profile_dir(session_key)
        launch_handle = launch_chromium(profile_dir=profile_dir)
        session_info.launch_handle = launch_handle
        session_info.profile_dir = profile_dir
        cdp_url = launch_handle.cdp_url
        auto_owned = True

    policy, timeout_s = _get_dialog_policy_config()
    return SUPERVISOR_REGISTRY.get_or_start(
        session_key,
        cdp_url,
        launch_handle=session_info.launch_handle,
        auto_owned=auto_owned,
        dialog_policy=policy,
        dialog_timeout_s=timeout_s,
    )


def _reject_redirect(final_url: str, original_url: str, allow_local: bool) -> str | None:
    """重定向 SSRF + 站点策略二次校验；命中即返回错误 JSON，否则 None。"""
    if not final_url or final_url == original_url:
        return None
    blocked = check_website_access(final_url)
    if blocked:
        return json.dumps(
            {
                "success": False,
                "error": blocked.message,
                "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source},
            },
            ensure_ascii=False,
        )
    if is_always_blocked_url(final_url):
        return json.dumps(
            {"success": False, "error": "Blocked: redirect landed on a cloud metadata endpoint"},
            ensure_ascii=False,
        )
    if not allow_local and not _allow_private_urls() and not is_safe_url(final_url):
        return json.dumps(
            {"success": False, "error": "Blocked: redirect landed on a private/internal address"},
            ensure_ascii=False,
        )
    return None


def browser_navigate(url: str, task_id: str | None = None) -> str:
    """导航到指定 URL 并返回 JSON 结果（含首屏快照、跳转后 SSRF 校验、bot 检测提示）。"""
    url_decoded = unquote(url)
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(url_decoded):
        return json.dumps(
            {
                "success": False,
                "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
            },
            ensure_ascii=False,
        )

    url = normalize_url_for_request(url)
    normalized_decoded = unquote(url)
    if SECRET_PREFIX_RE.search(url) or SECRET_PREFIX_RE.search(normalized_decoded):
        return json.dumps(
            {
                "success": False,
                "error": "Blocked: URL contains what appears to be an API key or token. Secrets must not be sent in URLs.",
            },
            ensure_ascii=False,
        )

    effective_task_id = task_id or "default"
    nav_session_key = _navigation_session_key(effective_task_id, url)
    auto_local_this_nav = nav_session_key.endswith("::local")
    allow_local = auto_local_this_nav or _allow_private_urls()

    if is_always_blocked_url(url):
        return json.dumps(
            {"success": False, "error": "Blocked: URL targets a cloud metadata endpoint"},
            ensure_ascii=False,
        )

    if not allow_local and not is_safe_url(url):
        return json.dumps(
            {"success": False, "error": "Blocked: URL targets a private or internal address"},
            ensure_ascii=False,
        )

    blocked = check_website_access(url)
    if blocked:
        return json.dumps(
            {
                "success": False,
                "error": blocked.message,
                "blocked_by_policy": {"host": blocked.host, "rule": blocked.rule, "source": blocked.source},
            },
            ensure_ascii=False,
        )

    if is_camofox_mode():
        return camofox_navigate(url, task_id)

    try:
        supervisor = _ensure_supervisor(nav_session_key)
        _last_active_session_key[effective_task_id] = nav_session_key
        touch_session(nav_session_key)

        nav_res = supervisor.navigate(url)
        final_url = nav_res.get("url", url)
        title = nav_res.get("title", "")

        reject = _reject_redirect(final_url, url, allow_local)
        if reject is not None:
            supervisor.navigate("about:blank")
            return reject

        response: dict[str, Any] = {"success": True, "url": final_url, "title": title}

        title_lower = title.lower()
        if any(p in title_lower for p in BLOCKED_PATTERNS):
            response["bot_detection_warning"] = (
                f"Page title '{title}' suggests bot detection. The site may have blocked this request. "
                "Options: 1) Try adding delays between actions, 2) Access different pages first, "
                "3) Switch to Camofox remote browser (set `browser.camofox.url` in Desktop settings), "
                "4) Some sites have aggressive bot detection that may be unavoidable."
            )

        try:
            snap_res = supervisor.snapshot_axtree(interactive_only=True)
            if snap_res.get("ok"):
                snap_text = snap_res.get("snapshot", "")
                if len(snap_text) > SNAPSHOT_SUMMARIZE_THRESHOLD:
                    snap_text = _truncate_snapshot(snap_text)
                response["snapshot"] = snap_text
                response["element_count"] = snap_res.get("element_count", 0)
        except Exception as e:
            logger.debug("Auto-snapshot after navigate failed: %s", e)

        return json.dumps(response, ensure_ascii=False)

    except Exception as e:
        logger.warning("browser_navigate failed: %s", e)
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


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
