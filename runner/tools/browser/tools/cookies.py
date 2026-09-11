import json
from typing import Any

from ...registry import registry
from ..check import check_browser_native_requirements
from ..schemas import (
    BROWSER_COOKIES_CLEAR_SCHEMA,
    BROWSER_COOKIES_GET_SCHEMA,
    BROWSER_COOKIES_SET_SCHEMA,
    BROWSER_STORAGE_GET_SCHEMA,
    BROWSER_STORAGE_SET_SCHEMA,
)
from ._common import browser_session, no_supervisor


def browser_cookies_get(url: str | None = None, task_id: str | None = None) -> str:
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
                from urllib.parse import urlparse

                current_host = (urlparse(current_url).hostname or "").lower().lstrip(".")
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
        payload: dict[str, Any] = {"success": True, "name": name, "domain": domain}
        if warning:
            payload["warning"] = warning
        return json.dumps(payload, ensure_ascii=False)


def browser_cookies_clear(session: bool = True, storage: bool = True, task_id: str | None = None) -> str:
    """清空当前源的 cookie 和 storage。

    Network.clearBrowserCookies 是全局操作（无 origin 限定），
    Storage.clearDataForOrigin 当前实现传 ``"*"`` 也清空所有源。
    如需精确按源清理，需后续补充按 origin 删除单条 cookie 的实现。
    """
    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        actions: list[str] = []
        if session:
            if not supervisor.send_cdp("Network.clearBrowserCookies", {}).get("ok"):
                return json.dumps({"success": False, "error": "Network.clearBrowserCookies failed"}, ensure_ascii=False)
            actions.append("session_cookies")
        if storage:
            if not supervisor.send_cdp("Storage.clearDataForOrigin", {"origin": "*", "storageTypes": "all"}).get("ok"):
                return json.dumps({"success": False, "error": "Storage.clearDataForOrigin failed"}, ensure_ascii=False)
            actions.append("storage")
        return json.dumps({"success": True, "cleared": actions, "scope": "all_origins"}, ensure_ascii=False)


def browser_storage_get(key: str, origin: str, kind: str = "localStorage", task_id: str | None = None) -> str:
    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.send_cdp(
            "DOMStorage.getDOMStorageItems",
            {"storageId": {"securityOrigin": origin, "isLocalStorage": kind == "localStorage"}},
        )
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        items = res.get("result", {}).get("entries", [])
        for entry in items:
            # CDP ``entries`` 是 list 数组而非强类型 ``[key, value]`` tuple; 长度为 0 或 1 的项(空 value)
            # 必须跳过, 否则 ``entry[1]`` 直接抛 IndexError 拖垮整次调用。
            if not isinstance(entry, list) or len(entry) < 1:
                continue
            if entry[0] == key:
                value = entry[1] if len(entry) >= 2 else None
                return json.dumps(
                    {"success": True, "key": key, "value": value, "kind": kind, "found": True},
                    ensure_ascii=False,
                )
        return json.dumps(
            {"success": True, "key": key, "value": None, "kind": kind, "found": False},
            ensure_ascii=False,
        )


def browser_storage_set(
    key: str,
    value: str,
    origin: str,
    kind: str = "localStorage",
    task_id: str | None = None,
) -> str:
    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.send_cdp(
            "DOMStorage.setDOMStorageItem",
            {
                "storageId": {"securityOrigin": origin, "isLocalStorage": kind == "localStorage"},
                "key": key,
                "value": value,
            },
        )
        if not res.get("ok"):
            return json.dumps({"success": False, "error": res.get("error", "unknown error")}, ensure_ascii=False)
        return json.dumps({"success": True, "key": key, "kind": kind, "origin": origin}, ensure_ascii=False)


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
        session=args.get("session", True),
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
