"""固定自托管接口的登录与内容读取；不读取或返回平台 Cookie。"""

import asyncio
import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from io import BytesIO
from struct import error as StructError
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from infrastructure.network import trusted_service_async_client
from infrastructure.text import utc_now

from .social_content import RequestGuard, SocialSourceError

_MAX_RESPONSE_BYTES = 1500000
_MAX_QR_BYTES = 512000
_TIMEOUT_SECONDS = 60


class XhsLoginStatus(BaseModel):
    is_logged_in: bool
    username: str | None = None
    user_id: str | None = None


class XhsLoginQrCode(BaseModel):
    is_logged_in: bool
    image: str | None = None
    expires_at: datetime | None = None


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


def _qr_seconds(value: Any) -> int:
    if not isinstance(value, str):
        raise SocialSourceError("Xiaohongshu QR expiry is missing", code=422, reason="response_invalid")
    match = re.fullmatch(r"(?:(\d{1,2})m)?(\d{1,3})s", value)
    seconds = int(match[1] or 0) * 60 + int(match[2]) if match else 0
    if not 0 < seconds <= 240:
        raise SocialSourceError("Xiaohongshu QR expiry is invalid", code=422, reason="response_invalid")
    return seconds


class XiaohongshuServiceClient:
    def __init__(self, *, base_url: str, auth_token: str = "") -> None:
        self._base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(self._base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port == 0
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "?" in self._base_url
            or "#" in self._base_url
            or "\\" in self._base_url
            or any(character.isspace() for character in self._base_url)
        ):
            raise ValueError("Xiaohongshu requires an operator-configured service origin")
        self._auth_token = auth_token.strip()
        if any(ord(character) < 33 or ord(character) > 126 for character in self._auth_token):
            raise ValueError("Invalid configured source authentication token")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> dict[str, Any]:
        if (method, path) not in {
            ("GET", "/api/v1/login/status"),
            ("GET", "/api/v1/login/qrcode"),
            ("DELETE", "/api/v1/login/cookies"),
            ("POST", "/api/v1/feeds/search"),
            ("POST", "/api/v1/feeds/detail"),
        }:
            raise ValueError("Unsupported Xiaohongshu service operation")
        headers = {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else {}
        if before_request is not None:
            await before_request()
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with trusted_service_async_client(
                    self._base_url,
                    timeout=httpx.Timeout(30, connect=10, pool=10),
                ) as client:
                    async with client.stream(
                        method,
                        self._base_url + path,
                        headers=headers,
                        **({"json": payload} if payload is not None else {}),
                    ) as response:
                        if not 200 <= response.status_code < 300:
                            raise SocialSourceError(
                                "Xiaohongshu service rejected the operation",
                                code=response.status_code,
                                reason=(
                                    "login_required"
                                    if response.status_code in {401, 403}
                                    else "rate_limited"
                                    if response.status_code == 429
                                    else "source_failed"
                                ),
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                                raise SocialSourceError(
                                    "Xiaohongshu response exceeded its limit",
                                    code=422,
                                    reason="response_invalid",
                                )
                            body.extend(chunk)
            raw = json.loads(body)
        except (httpx.HTTPError, TimeoutError):
            raise SocialSourceError("Xiaohongshu service is unavailable", reason="network_error") from None
        except (ValueError, UnicodeError, RecursionError):
            raise SocialSourceError(
                "Xiaohongshu service response is invalid",
                code=422,
                reason="response_invalid",
            ) from None
        if not isinstance(raw, dict) or raw.get("success") is not True or not isinstance(raw.get("data"), dict):
            raise SocialSourceError(
                "Xiaohongshu service returned an invalid result",
                code=422,
                reason="response_invalid",
            )
        return raw["data"]

    async def status(self, *, before_request: RequestGuard | None = None) -> XhsLoginStatus:
        data = await self._request("GET", "/api/v1/login/status", before_request=before_request)
        logged_in = data.get("is_logged_in")
        if not isinstance(logged_in, bool):
            raise SocialSourceError("Xiaohongshu login status is invalid", code=422, reason="response_invalid")
        return XhsLoginStatus(
            is_logged_in=logged_in,
            username=data["username"][:160] if logged_in and isinstance(data.get("username"), str) else None,
            user_id=data["user_id"][:96] if logged_in and isinstance(data.get("user_id"), str) else None,
        )

    async def qrcode(self) -> XhsLoginQrCode:
        started_at = utc_now()
        data = await self._request("GET", "/api/v1/login/qrcode")
        if not isinstance(data.get("is_logged_in"), bool):
            raise SocialSourceError("Xiaohongshu QR login state is invalid", code=422, reason="response_invalid")
        if data["is_logged_in"]:
            return XhsLoginQrCode(is_logged_in=True)
        return XhsLoginQrCode(
            is_logged_in=False,
            image=_qr_png(data.get("img")),
            expires_at=started_at + timedelta(seconds=_qr_seconds(data.get("timeout"))),
        )

    async def clear_saved_login(self) -> None:
        await self._request("DELETE", "/api/v1/login/cookies")

    async def search_feeds(self, keyword: str, *, before_request: RequestGuard | None = None) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/feeds/search",
            payload={"keyword": keyword, "filters": {"sort_by": "最新", "publish_time": "一天内"}},
            before_request=before_request,
        )

    async def feed_detail(
        self,
        source_id: str,
        token: str,
        *,
        before_request: RequestGuard | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/feeds/detail",
            payload={"feed_id": source_id, "xsec_token": token, "load_all_comments": False},
            before_request=before_request,
        )
