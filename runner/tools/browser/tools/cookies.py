import json
from typing import Any
from urllib.parse import urlsplit

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import (
    BROWSER_COOKIES_CLEAR_SCHEMA,
    BROWSER_COOKIES_GET_SCHEMA,
    BROWSER_COOKIES_SET_SCHEMA,
    BROWSER_STORAGE_GET_SCHEMA,
    BROWSER_STORAGE_SET_SCHEMA,
)
from ._common import browser_session, camofox_unsupported, no_supervisor


def browser_cookies_get(url: str | None = None, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_cookies_get")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        params: dict[str, Any] = {}
        if url:
            params["urls"] = [url]
        res = supervisor.send_cdp("Network.getCookies", params)
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        cookies = res.get("result", {}).get("cookies", [])
        return json.dumps({"success": True, "count": len(cookies), "cookies": cookies}, ensure_ascii=False)


def browser_cookies_set(
    name: str,
    value: str,
    domain: str,
    path: str = "/",
    expires: float | None = None,
    http_only: bool = False,
    secure: bool = False,
    same_site: str | None = None,
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_cookies_set")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        params: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "httpOnly": http_only,
            "secure": secure,
        }
        if expires is not None:
            params["expires"] = expires
        if same_site is not None:
            params["sameSite"] = same_site

        # 软守卫：domain 与当前页 hostname 不一致时，cookie 不会随当前请求发出。
        # 仍按调用方请求设置（可能用于跨站测试），但 result 附 warning 让模型看见。
        warning: str | None = None
        frame_res = supervisor.send_cdp("Page.getFrameTree")
        if frame_res.get("ok"):
            frame = (frame_res.get("result") or {}).get("frameTree", {}).get("frame", {})
            current_url = str(frame.get("url") or "")
            if current_url:
                current_host = (urlsplit(current_url).hostname or "").lower().lstrip(".")
                requested_host = domain.lower().lstrip(".")
                # 子域关系：requested=example.com 时 *.example.com 都算匹配
                if (
                    current_host
                    and requested_host
                    and not (current_host == requested_host or current_host.endswith("." + requested_host))
                ):
                    warning = (
                        f"Cookie domain {domain!r} does not match current page hostname "
                        f"{current_host!r} — the cookie will not be sent with requests to "
                        f"{current_host!r}. If you intended a different scope, retry with "
                        f"domain matching the target site."
                    )

        res = supervisor.send_cdp("Network.setCookie", params)
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        if res.get("result", {}).get("success") is False:
            return json.dumps(
                {
                    "success": False,
                    "error": "The browser rejected the cookie; check domain, path, secure and sameSite "
                    "(sameSite 'None' requires secure=true).",
                },
                ensure_ascii=False,
            )
        payload: dict[str, Any] = {"success": True, "name": name, "domain": domain}
        if warning:
            payload["warning"] = warning
        return json.dumps(payload, ensure_ascii=False)


def browser_cookies_clear(cookies: bool = True, storage: bool = True, task_id: str | None = None) -> str:
    """清空浏览器全部 cookie 和/或全部源的站点存储；两者都是全局操作，不限当前源。"""
    if is_camofox_mode():
        return camofox_unsupported("browser_cookies_clear")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        cleared: list[str] = []
        if cookies:
            res = supervisor.send_cdp("Network.clearBrowserCookies", {})
            if not res.get("ok"):
                return json.dumps({"success": False, "error": res.get("error"), "cleared": cleared}, ensure_ascii=False)
            cleared.append("cookies")
        if storage:
            res = supervisor.send_cdp("Storage.clearDataForOrigin", {"origin": "*", "storageTypes": "all"})
            if not res.get("ok"):
                return json.dumps({"success": False, "error": res.get("error"), "cleared": cleared}, ensure_ascii=False)
            cleared.append("storage")
        return json.dumps({"success": True, "cleared": cleared, "scope": "all_origins"}, ensure_ascii=False)


def _storage_id(origin: str, kind: str) -> dict[str, Any]:
    """DOMStorage 按源精确匹配；把带路径或尾斜杠的 URL 收敛为 ``scheme://host[:port]``。"""
    parts = urlsplit(origin.strip())
    security_origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else origin
    return {"securityOrigin": security_origin, "isLocalStorage": kind == "localStorage"}


def browser_storage_get(key: str, origin: str, kind: str = "localStorage", task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_storage_get")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        storage_id = _storage_id(origin, kind)
        res = supervisor.send_cdp("DOMStorage.getDOMStorageItems", {"storageId": storage_id})
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        payload: dict[str, Any] = {"key": key, "kind": kind, "origin": storage_id["securityOrigin"]}
        for entry_key, entry_value in res.get("result", {}).get("entries", []):
            if entry_key == key:
                return json.dumps({"success": True, **payload, "value": entry_value, "found": True}, ensure_ascii=False)
        return json.dumps({"success": True, **payload, "value": None, "found": False}, ensure_ascii=False)


def browser_storage_set(
    key: str,
    value: str,
    origin: str,
    kind: str = "localStorage",
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_storage_set")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        storage_id = _storage_id(origin, kind)
        res = supervisor.send_cdp(
            "DOMStorage.setDOMStorageItem",
            {"storageId": storage_id, "key": key, "value": value},
        )
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        return json.dumps(
            {"success": True, "key": key, "kind": kind, "origin": storage_id["securityOrigin"]},
            ensure_ascii=False,
        )


registry.register_tool(
    "browser_cookies_get",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_COOKIES_GET_SCHEMA,
)(
    lambda args, **kw: browser_cookies_get(url=args.get("url"), task_id=kw.get("task_id")),
)

registry.register_tool(
    "browser_cookies_set",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_COOKIES_SET_SCHEMA,
)(
    lambda args, **kw: browser_cookies_set(
        name=args.get("name", ""),
        value=args.get("value", ""),
        domain=args.get("domain", ""),
        path=args.get("path", "/"),
        expires=args.get("expires"),
        http_only=args.get("httpOnly", False),
        secure=args.get("secure", False),
        same_site=args.get("sameSite"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_cookies_clear",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_COOKIES_CLEAR_SCHEMA,
)(
    lambda args, **kw: browser_cookies_clear(
        cookies=args.get("cookies", True),
        storage=args.get("storage", True),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_storage_get",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_STORAGE_GET_SCHEMA,
)(
    lambda args, **kw: browser_storage_get(
        key=args.get("key", ""),
        origin=args.get("origin", ""),
        kind=args.get("kind", "localStorage"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_storage_set",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_STORAGE_SET_SCHEMA,
)(
    lambda args, **kw: browser_storage_set(
        key=args.get("key", ""),
        value=args.get("value", ""),
        origin=args.get("origin", ""),
        kind=args.get("kind", "localStorage"),
        task_id=kw.get("task_id"),
    ),
)
