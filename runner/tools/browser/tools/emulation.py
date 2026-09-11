import json
import math

from ...registry import registry
from ..camofox import is_camofox_mode
from ..check import check_browser_native_requirements
from ..schemas import (
    BROWSER_SET_EXTRA_HEADERS_SCHEMA,
    BROWSER_SET_GEOLOCATION_SCHEMA,
    BROWSER_SET_USER_AGENT_SCHEMA,
    BROWSER_SET_VIEWPORT_SCHEMA,
)
from ._common import browser_session, camofox_unsupported, no_supervisor


def browser_set_viewport(
    width: int,
    height: int,
    device_scale_factor: float = 1.0,
    mobile: bool = False,
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_set_viewport")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        params = {
            "width": int(width),
            "height": int(height),
            "deviceScaleFactor": float(device_scale_factor),
            "mobile": bool(mobile),
        }
        res = supervisor.send_cdp("Emulation.setDeviceMetricsOverride", params)
        if res.get("ok"):
            return json.dumps({"success": True, "viewport": {"width": width, "height": height, "mobile": mobile}})
        return json.dumps({"success": False, "error": res.get("error", "Failed to set viewport")})


def browser_set_user_agent(
    user_agent: str | None = None,
    platform: str | None = None,
    accept_language: str | None = None,
    task_id: str | None = None,
) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_set_user_agent")

    if user_agent is None and platform is None and accept_language is None:
        return json.dumps(
            {"success": False, "error": "At least one of user_agent, platform, or accept_language is required"},
            ensure_ascii=False,
        )

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        params: dict = {"userAgent": user_agent or ""}
        if platform:
            params["platform"] = platform
        if accept_language:
            params["acceptLanguage"] = accept_language

        res = supervisor.send_cdp("Network.setUserAgentOverride", params)
        if res.get("ok"):
            return json.dumps({"success": True, "user_agent": user_agent, "cleared": user_agent is None})
        return json.dumps({"success": False, "error": res.get("error", "Failed to set user agent")})


def browser_set_extra_headers(headers: dict[str, str], task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_set_extra_headers")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        res = supervisor.send_cdp("Network.setExtraHTTPHeaders", {"headers": headers or {}})
        if res.get("ok"):
            return json.dumps({"success": True, "headers_count": len(headers) if headers else 0})
        return json.dumps({"success": False, "error": res.get("error", "Failed to set extra headers")})


def browser_set_geolocation(lat: float, lon: float, accuracy: float = 100.0, task_id: str | None = None) -> str:
    if is_camofox_mode():
        return camofox_unsupported("browser_set_geolocation")

    with browser_session(task_id) as (supervisor, _):
        if supervisor is None:
            return no_supervisor()

        if math.isnan(lat) or math.isnan(lon):
            res = supervisor.send_cdp("Emulation.clearGeolocationOverride", {})
            if res.get("ok"):
                return json.dumps({"success": True, "cleared": True})
            return json.dumps({"success": False, "error": res.get("error", "Failed to clear geolocation")})

        params = {"latitude": float(lat), "longitude": float(lon), "accuracy": float(accuracy)}
        res = supervisor.send_cdp("Emulation.setGeolocationOverride", params)
        if res.get("ok"):
            return json.dumps({"success": True, "latitude": lat, "longitude": lon})
        return json.dumps({"success": False, "error": res.get("error", "Failed to set geolocation")})


registry.register_tool(
    "browser_set_viewport",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_SET_VIEWPORT_SCHEMA,
)(
    lambda args, **kw: browser_set_viewport(
        width=args.get("width", 1280),
        height=args.get("height", 800),
        device_scale_factor=args.get("device_scale_factor", 1.0),
        mobile=args.get("mobile", False),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_set_user_agent",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_SET_USER_AGENT_SCHEMA,
)(
    lambda args, **kw: browser_set_user_agent(
        user_agent=args.get("user_agent"),
        platform=args.get("platform"),
        accept_language=args.get("accept_language"),
        task_id=kw.get("task_id"),
    ),
)

registry.register_tool(
    "browser_set_extra_headers",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_SET_EXTRA_HEADERS_SCHEMA,
)(
    lambda args, **kw: browser_set_extra_headers(headers=args.get("headers", {}), task_id=kw.get("task_id")),
)

registry.register_tool(
    "browser_set_geolocation",
    check_fn=check_browser_native_requirements,
    schema=BROWSER_SET_GEOLOCATION_SCHEMA,
)(
    lambda args, **kw: browser_set_geolocation(
        lat=args.get("lat", 0.0),
        lon=args.get("lon", 0.0),
        accuracy=args.get("accuracy", 100.0),
        task_id=kw.get("task_id"),
    ),
)
