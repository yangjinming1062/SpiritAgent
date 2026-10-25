"""平台注册的凭证校验、可用性与账户独立适配器工厂。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel, JsonValue

from infrastructure.config import SETTINGS

from .social_content import RequestGuard, SocialContent, SocialSearchPage, SocialSourceError
from .social_credentials import XCredentials, XiaohongshuCredentials
from .social_login import SocialLoginClient
from .social_platforms import get_social_platform
from .x_source import XSource
from .xiaohongshu import XiaohongshuServiceClient
from .xiaohongshu_source import XiaohongshuSource


class SocialSource(Protocol):
    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage: ...

    async def detail(
        self,
        candidate: SocialContent,
        *,
        before_request: RequestGuard | None = None,
    ) -> SocialContent | None: ...


@dataclass(frozen=True)
class PlatformAdapter:
    credentials: type[BaseModel]
    source: Callable[[dict[str, JsonValue]], SocialSource]
    available: Callable[[], bool]
    manual_credentials: Callable[[str], dict[str, JsonValue]] | None = None
    login: Callable[[], SocialLoginClient] | None = None
    runtime_identity: Callable[[], str] | None = None


class _BoundSource:
    """每次出站前核对采集服务身份，防止配置轮换后继续使用旧连接。"""

    def __init__(self, source: SocialSource, identity: str, current_identity: Callable[[], str]) -> None:
        self._source = source
        self._identity = identity
        self._current_identity = current_identity

    def _require_current(self) -> None:
        if self._current_identity() != self._identity:
            raise SocialSourceError("Social source configuration changed", code=409, reason="configuration_changed")

    def _guard(self, before_request: RequestGuard | None) -> RequestGuard:
        async def guard() -> None:
            self._require_current()
            if before_request is not None:
                await before_request()
            self._require_current()

        return guard

    async def search(
        self,
        query: str,
        *,
        window_start: datetime,
        window_end: datetime,
        cursor: dict[str, Any] | None = None,
        before_request: RequestGuard | None = None,
    ) -> SocialSearchPage:
        return await self._source.search(
            query,
            window_start=window_start,
            window_end=window_end,
            cursor=cursor,
            before_request=self._guard(before_request),
        )

    async def detail(
        self,
        candidate: SocialContent,
        *,
        before_request: RequestGuard | None = None,
    ) -> SocialContent | None:
        return await self._source.detail(candidate, before_request=self._guard(before_request))


def _x_source(credentials: dict[str, JsonValue]) -> XSource:
    return XSource(cookies=XCredentials.model_validate(credentials).cookies)


def _x_manual_credentials(raw: str) -> dict[str, JsonValue]:
    return XCredentials(cookies=raw).model_dump(mode="json")


def _x_available() -> bool:
    return True


def _xiaohongshu_available() -> bool:
    return bool(SETTINGS.xhs_mcp_base_url and SETTINGS.xhs_mcp_auth_token)


def _xiaohongshu_identity() -> str:
    return sha256(f"{SETTINGS.xhs_mcp_base_url}\x00{SETTINGS.xhs_mcp_auth_token}".encode()).hexdigest()


def _xiaohongshu_client() -> XiaohongshuServiceClient:
    return XiaohongshuServiceClient(base_url=SETTINGS.xhs_mcp_base_url, auth_token=SETTINGS.xhs_mcp_auth_token)


def _xiaohongshu_source(credentials: dict[str, JsonValue]) -> XiaohongshuSource:
    saved = XiaohongshuCredentials.model_validate(credentials)
    return XiaohongshuSource(
        client=_xiaohongshu_client(),
        cookies=[cookie.model_dump(mode="json", exclude_none=True) for cookie in saved.cookies],
    )


SOCIAL_ADAPTERS: dict[str, PlatformAdapter] = {
    "x": PlatformAdapter(
        credentials=XCredentials,
        source=_x_source,
        available=_x_available,
        manual_credentials=_x_manual_credentials,
    ),
    "xiaohongshu": PlatformAdapter(
        credentials=XiaohongshuCredentials,
        source=_xiaohongshu_source,
        available=_xiaohongshu_available,
        login=_xiaohongshu_client,
        runtime_identity=_xiaohongshu_identity,
    ),
}


def _adapter(platform: str) -> PlatformAdapter:
    get_social_platform(platform)
    try:
        return SOCIAL_ADAPTERS[platform]
    except KeyError:
        raise ValueError("Social platform adapter is not registered") from None


def validate_social_credentials(platform: str, credentials: object) -> dict[str, JsonValue]:
    return _adapter(platform).credentials.model_validate(credentials).model_dump(mode="json", exclude_none=True)


def parse_social_credentials(platform: str, raw: str) -> dict[str, JsonValue]:
    parser = _adapter(platform).manual_credentials
    if parser is None:
        raise ValueError("This platform requires account login")
    return parser(raw)


def source_available(platform: str) -> bool:
    return _adapter(platform).available()


def create_social_client(platform: str, credentials: dict[str, JsonValue]) -> SocialSource:
    adapter = _adapter(platform)
    if not adapter.available():
        raise ValueError("Social platform service is unavailable")
    identity = adapter.runtime_identity() if adapter.runtime_identity is not None else None
    source = adapter.source(credentials)
    if adapter.runtime_identity is not None and identity is not None:
        return _BoundSource(source, identity, adapter.runtime_identity)
    return source


def create_social_login_client(platform: str) -> SocialLoginClient:
    adapter = _adapter(platform)
    if adapter.login is None:
        raise ValueError("Social platform does not support interactive login")
    if not adapter.available():
        raise ValueError("Social platform service is unavailable")
    return adapter.login()
