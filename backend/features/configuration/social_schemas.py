"""账户资讯来源的公开状态与仅供后端使用的凭证快照。"""

from dataclasses import dataclass, field
from typing import Literal

from infrastructure.web.social_login import SocialLoginStatus
from infrastructure.web.social_platforms import SocialAuthKind, SocialPlatform
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SocialAccountView(BaseModel):
    platform: SocialPlatform
    auth_kind: SocialAuthKind
    configured: bool
    state: Literal["unconfigured", "configured", "login_required"]
    login: SocialLoginStatus | None = None


class SocialAccountList(BaseModel):
    accounts: list[SocialAccountView]


class SocialCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentials: str = Field(min_length=1, max_length=30000, repr=False)


class SocialVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


@dataclass(frozen=True)
class SocialCredentialSnapshot:
    revision: str
    credentials: dict[str, JsonValue] = field(repr=False)
