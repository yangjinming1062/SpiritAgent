"""平台注册的凭证校验与账户独立适配器工厂。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, JsonValue

from .social_content import RequestGuard, SocialContent, SocialSearchPage
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
    manual_credentials: Callable[[str], dict[str, JsonValue]] | None = None
    login: Callable[[], SocialLoginClient] | None = None


def _x_source(credentials: dict[str, JsonValue]) -> XSource:
    return XSource(cookies=XCredentials.model_validate(credentials).cookies)


def _x_manual_credentials(raw: str) -> dict[str, JsonValue]:
    return XCredentials(cookies=raw).model_dump(mode="json")


def _xiaohongshu_source(credentials: dict[str, JsonValue]) -> XiaohongshuSource:
    saved = XiaohongshuCredentials.model_validate(credentials)
    return XiaohongshuSource(
        client=XiaohongshuServiceClient(),
        cookies=[cookie.model_dump(mode="json", exclude_none=True) for cookie in saved.cookies],
    )


SOCIAL_ADAPTERS: dict[str, PlatformAdapter] = {
    "x": PlatformAdapter(
        credentials=XCredentials,
        source=_x_source,
        manual_credentials=_x_manual_credentials,
    ),
    "xiaohongshu": PlatformAdapter(
        credentials=XiaohongshuCredentials,
        source=_xiaohongshu_source,
        login=XiaohongshuServiceClient,
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


def create_social_client(platform: str, credentials: dict[str, JsonValue]) -> SocialSource:
    return _adapter(platform).source(credentials)


def create_social_login_client(platform: str) -> SocialLoginClient:
    adapter = _adapter(platform)
    if adapter.login is None:
        raise ValueError("Social platform does not support interactive login")
    return adapter.login()
