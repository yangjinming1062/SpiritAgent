"""账户隔离的采集服务接口；平台 Cookie 只在 Backend 与内部服务间传递。"""

import asyncio
import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from io import BytesIO
from struct import error as StructError
from typing import Any, Literal

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, JsonValue, ValidationError

from infrastructure.network import read_capped_response, trusted_service_async_client
from infrastructure.text import utc_now

from .social_content import RequestGuard, SocialSourceError, failure_reason
from .social_credentials import XiaohongshuCredentials
from .social_login import LoginState, SocialLoginQrCode, SocialLoginResult, SocialLoginVerification

_BASE_URL = "http://xiaohongshu-mcp:18060"
_MAX_RESPONSE_BYTES = 1500000
_MAX_QR_BYTES = 512000
_TIMEOUT_SECONDS = 60
_SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{16,96}$"
_LOGIN_FAILURE_REASONS = {
    "LOGIN_PAGE_CHECK_FAILED": "login_page_check_failed",
    "LOGIN_CODE_INPUT_FAILED": "login_code_input_failed",
    "LOGIN_CONFIRM_BUTTON_FAILED": "login_confirm_button_failed",
    "LOGIN_COOKIE_SAVE_FAILED": "login_cookie_save_failed",
}


class _LoginSession(BaseModel):
    session_id: str = Field(pattern=_SESSION_ID_PATTERN)
    state: LoginState
    expires_at: datetime
    verification_error: Literal["invalid_code"] | None = None
    cookies: list[dict[str, JsonValue]] | None = Field(default=None, repr=False)


def _session_status(data: dict[str, Any], session_id: str) -> SocialLoginResult:
    try:
        result = _LoginSession.model_validate(data)
        if result.session_id != session_id or result.expires_at.tzinfo is None:
            raise ValueError("Invalid login session binding")
        if result.expires_at > utc_now() + timedelta(seconds=245):
            raise ValueError("Invalid login session expiry")
        if result.state == "logged_in":
            XiaohongshuCredentials.model_validate({"cookies": result.cookies})
        elif result.cookies is not None:
            raise ValueError("Unfinished login returned cookies")
    except (ValidationError, ValueError):
        raise SocialSourceError("Xiaohongshu login session is invalid", code=422, reason="response_invalid") from None
    return SocialLoginResult(
        **result.model_dump(exclude={"cookies"}),
        credentials={"cookies": result.cookies} if result.cookies is not None else None,
    )


def _validate_owner(owner: str) -> None:
    if not re.fullmatch(_SESSION_ID_PATTERN, owner):
        raise ValueError("Invalid Xiaohongshu connection owner")


def _qr_png(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("data:image/png;base64,"):
        raise SocialSourceError("Xiaohongshu did not return a PNG QR image", code=422, reason="response_invalid")
    try:
        decoded = base64.b64decode(value.partition(",")[2], validate=True)
        if not decoded or len(decoded) > _MAX_QR_BYTES:
            raise ValueError("Invalid QR image size")
        if decoded[:8] != b"\x89PNG\r\n\x1a\n" or decoded[12:16] != b"IHDR" or len(decoded) < 24:
            raise ValueError("Invalid QR image header")
        if not all(0 < int.from_bytes(decoded[start : start + 4], "big") <= 2048 for start in (16, 20)):
            raise ValueError("Invalid QR image dimensions")
        with Image.open(BytesIO(decoded)) as picture:
            if picture.format != "PNG" or not 0 < picture.width <= 2048 or not 0 < picture.height <= 2048:
                raise ValueError("Invalid QR image dimensions")
            picture.verify()
    except (
        binascii.Error,
        ValueError,
        OSError,
        SyntaxError,
        StructError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        raise SocialSourceError("Xiaohongshu QR image is invalid", code=422, reason="response_invalid") from None
    return "data:image/png;base64," + base64.b64encode(decoded).decode("ascii")


class XiaohongshuServiceClient:
    async def _request(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        before_request: RequestGuard | None = None,
    ) -> dict[str, Any]:
        if before_request is not None:
            await before_request()
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with trusted_service_async_client(
                    _BASE_URL,
                    timeout=httpx.Timeout(30, connect=10, pool=10),
                ) as client:
                    async with client.stream(
                        "POST",
                        _BASE_URL + path,
                        json=payload,
                    ) as response:
                        status_code = response.status_code
                        body = await read_capped_response(
                            response,
                            max_bytes=_MAX_RESPONSE_BYTES,
                            too_large=SocialSourceError(
                                "Xiaohongshu response exceeded its limit",
                                code=422,
                                reason="response_invalid",
                            ),
                        )
            try:
                raw = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                if 200 <= status_code < 300:
                    raise SocialSourceError(
                        "Xiaohongshu service response is invalid",
                        code=422,
                        reason="response_invalid",
                    ) from None
                raw = None
        except (httpx.HTTPError, TimeoutError):
            raise SocialSourceError("Xiaohongshu service is unavailable", reason="network_error") from None
        except (ValueError, UnicodeError, RecursionError):
            raise SocialSourceError(
                "Xiaohongshu service response is invalid",
                code=422,
                reason="response_invalid",
            ) from None
        if not 200 <= status_code < 300:
            reason = (
                "login_required"
                if isinstance(raw, dict) and raw.get("code") == "PLATFORM_LOGIN_REQUIRED"
                else "source_failed"
                if status_code in {401, 403}
                else "verification_unavailable"
                if status_code == 404
                else "session_expired"
                if status_code == 410
                else "session_conflict"
                if status_code == 409
                else "service_busy"
                if status_code == 503
                else failure_reason(status_code)
            )
            if (
                status_code == 502
                and path in {"/api/v1/accounts/login/session", "/api/v1/accounts/login/verification"}
                and isinstance(raw, dict)
                and isinstance(raw.get("code"), str)
            ):
                reason = _LOGIN_FAILURE_REASONS.get(raw["code"], reason)
            raise SocialSourceError("Xiaohongshu service rejected the operation", code=status_code, reason=reason)
        if not isinstance(raw, dict) or raw.get("success") is not True or not isinstance(raw.get("data"), dict):
            raise SocialSourceError(
                "Xiaohongshu service returned an invalid result",
                code=422,
                reason="response_invalid",
            )
        return raw["data"]

    async def qrcode(self, owner: str) -> SocialLoginQrCode:
        _validate_owner(owner)
        started_at = utc_now()
        data = await self._request("/api/v1/accounts/login/qrcode", payload={"owner": owner})
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not re.fullmatch(_SESSION_ID_PATTERN, session_id):
            raise SocialSourceError("Xiaohongshu login session ID is invalid", code=422, reason="response_invalid")
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
            if expires_at.tzinfo is None or not started_at < expires_at <= utc_now() + timedelta(seconds=245):
                raise ValueError("Invalid QR expiry")
        except (KeyError, TypeError, ValueError):
            raise SocialSourceError("Xiaohongshu QR expiry is invalid", code=422, reason="response_invalid") from None
        return SocialLoginQrCode(
            image=_qr_png(data.get("img")),
            expires_at=expires_at,
            session_id=session_id,
        )

    async def login_session(self, owner: str, session_id: str) -> SocialLoginResult:
        _validate_owner(owner)
        if not re.fullmatch(_SESSION_ID_PATTERN, session_id):
            raise ValueError("Invalid login session ID")
        data = await self._request(
            "/api/v1/accounts/login/session",
            payload={"owner": owner, "session_id": session_id},
        )
        return _session_status(data, session_id)

    async def submit_verification(self, owner: str, payload: SocialLoginVerification) -> SocialLoginResult:
        _validate_owner(owner)
        data = await self._request(
            "/api/v1/accounts/login/verification",
            payload={"owner": owner, **payload.model_dump()},
        )
        return _session_status(data, payload.session_id)

    async def cancel_login(self, owner: str, session_id: str) -> None:
        _validate_owner(owner)
        if not re.fullmatch(_SESSION_ID_PATTERN, session_id):
            raise ValueError("Invalid login session ID")
        await self._request(
            "/api/v1/accounts/login/cancel",
            payload={"owner": owner, "session_id": session_id},
        )

    async def search_feeds(
        self,
        keyword: str,
        *,
        cookies: list[dict[str, JsonValue]],
        before_request: RequestGuard | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "/api/v1/accounts/feeds/search",
            payload={"keyword": keyword, "cookies": cookies},
            before_request=before_request,
        )

    async def feed_detail(
        self,
        source_id: str,
        token: str,
        *,
        cookies: list[dict[str, JsonValue]],
        before_request: RequestGuard | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "/api/v1/accounts/feeds/detail",
            payload={"feed_id": source_id, "xsec_token": token, "cookies": cookies},
            before_request=before_request,
        )
