"""B 站原生接口客户端；扫码登录与采集只访问固定 API 主机，凭证只在 Backend 内流转。"""

import base64
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import qrcode
from qrcode.exceptions import DataOverflowError

from infrastructure.network import read_capped_response, safe_outbound_async_client
from infrastructure.text import utc_now

from .social_content import RequestGuard, SocialSourceError, failure_reason
from .social_login import SocialLoginQrCode, SocialLoginResult, SocialLoginVerification

_API_BASE = "https://api.bilibili.com"
_PASSPORT_BASE = "https://passport.bilibili.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RESPONSE_BYTES = 1500000
_QR_TTL = timedelta(seconds=180)
_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{16,96}$"
_COOKIE_NAMES = ("SESSDATA", "buvid3")
_POLL_STATES = {86101: "awaiting_scan", 86090: "verifying", 86038: "expired"}
# 轮询不返回二维码截止时刻；进程内记住首次生成的到期时间，避免每次轮询把界面倒计时重置回满值。
_QR_EXPIRY: dict[str, datetime] = {}
_QR_EXPIRY_LIMIT = 128
# web 前端的 WBI 密钥混淆表；签名必须与网页端算法一致，否则接口直接拒绝请求。
_WBI_MIXIN = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)


async def _get(
    base: str,
    path: str,
    *,
    params: dict[str, Any] | None,
    cookies: dict[str, str] | None,
    before_request: RequestGuard | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """请求固定 API 主机并返回 JSON 与响应 Cookie；登录回包靠后者取凭证。"""
    if base not in {_API_BASE, _PASSPORT_BASE} or not path.startswith("/"):
        raise SocialSourceError("Bilibili request target is not allowed", code=422, reason="response_invalid")
    if before_request is not None:
        await before_request()
    try:
        async with safe_outbound_async_client(
            timeout=httpx.Timeout(30, connect=10, pool=10),
            follow_redirects=False,
            headers={"user-agent": _USER_AGENT, "referer": "https://www.bilibili.com/"},
        ) as client:
            async with client.stream("GET", base + path, params=params, cookies=cookies) as response:
                status_code = response.status_code
                body = await read_capped_response(
                    response,
                    max_bytes=_MAX_RESPONSE_BYTES,
                    too_large=SocialSourceError(
                        "Bilibili response exceeded its limit", code=422, reason="response_invalid"
                    ),
                )
                delivered = dict(response.cookies.items())
    except (httpx.HTTPError, TimeoutError):
        raise SocialSourceError("Bilibili source network error", reason="network_error") from None
    if not 200 <= status_code < 300:
        raise SocialSourceError("Bilibili source request failed", code=status_code, reason=failure_reason(status_code))
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise SocialSourceError("Bilibili returned an invalid response", code=422, reason="response_invalid") from None
    if not isinstance(payload, dict):
        raise SocialSourceError("Bilibili returned an invalid response", code=422, reason="response_invalid")
    return payload, delivered


def _result(payload: dict[str, Any], *, missing_codes: frozenset[int] = frozenset()) -> tuple[Any, bool]:
    """校验平台业务码并取 data；missing_codes 内的业务码表示内容已不存在，按缺失返回。"""
    code = payload.get("code")
    if code == 0:
        return payload.get("data"), False
    if code in missing_codes:
        return None, True
    if code == -101:
        raise SocialSourceError("Bilibili login is required", code=401, reason="login_required")
    if code in {-412, -352, -799}:
        raise SocialSourceError("Bilibili source is rate limited", code=429, reason="rate_limited")
    raise SocialSourceError("Bilibili rejected the source query", code=422, reason="response_invalid")


def _wbi_key(url: Any) -> str:
    key = url.rsplit("/", 1)[-1].split(".")[0] if isinstance(url, str) else ""
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise SocialSourceError("Bilibili signing keys could not be parsed", code=422, reason="response_invalid")
    return key


def _wbi_value(value: Any) -> str:
    return "".join(character for character in str(value) if character not in "!'()*")


class BilibiliWebClient:
    """按账户 Cookie 读取搜索与视频详情；WBI 密钥实例内缓存，取密钥同样消耗请求预算。"""

    def __init__(self, *, cookies: dict[str, str]) -> None:
        self._cookies = cookies
        self._wbi_mixin: str | None = None

    async def _signed_params(self, params: dict[str, Any], before_request: RequestGuard | None) -> dict[str, Any]:
        if self._wbi_mixin is None:
            payload, _ = await _get(
                _API_BASE, "/x/web-interface/nav", params=None, cookies=self._cookies, before_request=before_request
            )
            data = payload.get("data")
            wbi_img = data.get("wbi_img") if isinstance(data, dict) else None
            img_url = wbi_img.get("img_url") if isinstance(wbi_img, dict) else None
            sub_url = wbi_img.get("sub_url") if isinstance(wbi_img, dict) else None
            mixed = _wbi_key(img_url) + _wbi_key(sub_url)
            self._wbi_mixin = "".join(mixed[index] for index in _WBI_MIXIN)[:32]
        signed = dict(params)
        signed["wts"] = int(time.time())
        query = urlencode({key: _wbi_value(value) for key, value in sorted(signed.items())})
        signed["w_rid"] = hashlib.md5((query + self._wbi_mixin).encode()).hexdigest()
        return signed

    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        page: int,
        before_request: RequestGuard | None,
    ) -> Any:
        params = await self._signed_params(
            {
                "search_type": "video",
                "keyword": query,
                "order": "pubdate",
                "page": page,
                "pubtime_begin_s": int(window_start.timestamp()),
                "pubtime_end_s": int(window_end.timestamp()),
            },
            before_request,
        )
        payload, _ = await _get(
            _API_BASE, "/x/web-interface/wbi/search/type", params=params, cookies=self._cookies, before_request=before_request
        )
        return _result(payload)[0]

    async def video_detail(self, bvid: str, *, before_request: RequestGuard | None) -> Any:
        payload, _ = await _get(
            _API_BASE,
            "/x/web-interface/view",
            params={"bvid": bvid},
            cookies=self._cookies,
            before_request=before_request,
        )
        # view 对不存在或已删除的视频返回 -400/-404，交给调用方当作内容消失丢弃。
        data, missing = _result(payload, missing_codes=frozenset({-400, -404}))
        return None if missing else data


def _remember_expiry(session_id: str, expires_at: datetime) -> datetime:
    if len(_QR_EXPIRY) >= _QR_EXPIRY_LIMIT:
        stale = [key for key, value in _QR_EXPIRY.items() if value <= utc_now()]
        for key in stale or list(_QR_EXPIRY)[: _QR_EXPIRY_LIMIT // 2]:
            _QR_EXPIRY.pop(key, None)
    _QR_EXPIRY[session_id] = expires_at
    return expires_at


def _session_expiry(session_id: str) -> datetime:
    """进程重启后没有记录，按新生成处理；平台返回 86038 才是权威到期。"""
    return _QR_EXPIRY.get(session_id) or _remember_expiry(session_id, utc_now() + _QR_TTL)


def _credential_cookies(delivered: dict[str, str], redirect: Any) -> dict[str, str]:
    """登录成功时凭证可能在响应 Cookie，也可能只在跨域跳转链接的查询串里。"""
    selected = {name: value for name, value in delivered.items() if name in _COOKIE_NAMES and value}
    link = redirect if isinstance(redirect, str) and len(redirect) <= 2048 else ""
    if link.startswith("https://"):
        for name, values in parse_qs(urlsplit(link).query).items():
            if name in _COOKIE_NAMES and values and values[0] and name not in selected:
                selected[name] = values[0]
    return selected


def _qr_png(link: str) -> str:
    try:
        buffer = BytesIO()
        qrcode.make(link).save(buffer, format="PNG")
        rendered = buffer.getvalue()
    except (DataOverflowError, ValueError, OSError):
        raise SocialSourceError("Bilibili QR image could not be rendered", code=502, reason="source_failed") from None
    return "data:image/png;base64," + base64.b64encode(rendered).decode("ascii")


class BilibiliLoginClient:
    """原生扫码登录；会话标识就是 qrcode_key，进程重启后按同一标识续查，不依赖本地状态。"""

    async def qrcode(self, owner: str) -> SocialLoginQrCode:
        started_at = utc_now()
        payload, _ = await _get(
            _PASSPORT_BASE, "/x/passport-login/web/qrcode/generate", params=None, cookies=None, before_request=None
        )
        data, _ = _result(payload)
        link = data.get("url") if isinstance(data, dict) else None
        key = data.get("qrcode_key") if isinstance(data, dict) else None
        if (
            not isinstance(link, str)
            or not link.startswith("https://")
            or len(link) > 2048
            or not isinstance(key, str)
            or not re.fullmatch(_SESSION_ID_PATTERN, key)
        ):
            raise SocialSourceError("Bilibili login session is invalid", code=422, reason="response_invalid")
        return SocialLoginQrCode(
            image=_qr_png(link), expires_at=_remember_expiry(key, started_at + _QR_TTL), session_id=key
        )

    async def login_session(self, owner: str, session_id: str) -> SocialLoginResult:
        if not re.fullmatch(_SESSION_ID_PATTERN, session_id):
            raise ValueError("Invalid login session ID")
        payload, cookies = await _get(
            _PASSPORT_BASE,
            "/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": session_id},
            cookies=None,
            before_request=None,
        )
        data, _ = _result(payload)
        if not isinstance(data, dict):
            raise SocialSourceError("Bilibili login session is invalid", code=422, reason="response_invalid")
        state = _POLL_STATES.get(data.get("code"))
        if state is not None:
            expires_at = _session_expiry(session_id) if state != "expired" else utc_now()
            if state == "expired":
                _QR_EXPIRY.pop(session_id, None)
            return SocialLoginResult(session_id=session_id, state=state, expires_at=expires_at)
        if data.get("code") != 0:
            raise SocialSourceError("Bilibili login session is invalid", code=422, reason="response_invalid")
        selected = _credential_cookies(cookies, data.get("url"))
        _QR_EXPIRY.pop(session_id, None)
        return SocialLoginResult(
            session_id=session_id, state="logged_in", expires_at=utc_now(), credentials={"cookies": selected}
        )

    async def submit_verification(self, owner: str, payload: SocialLoginVerification) -> SocialLoginResult:
        raise SocialSourceError("Bilibili login does not use verification codes", code=422, reason="response_invalid")

    async def cancel_login(self, owner: str, session_id: str) -> None:
        return None
