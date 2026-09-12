from datetime import datetime

from components import AIConfigPublic, AIConfigUpdate
from pydantic import BaseModel, ConfigDict, Field


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class ChatRequestClientContext(BaseModel):
    environment_hints: str | None = None
    platform_hints: str | None = None


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=2048)
    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_version: str = Field(default="desktop-app", max_length=64)
    client_context: ChatRequestClientContext | None = None


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    user: UserInfo


class UserModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_config: AIConfigUpdate


class UserModelConfigListItem(BaseModel):
    user_id: int
    ai_config: AIConfigPublic


class UserModelConfigListResponse(BaseModel):
    items: list[UserModelConfigListItem]
    ai_provider_support: dict[str, list[str]]
    system_ai_config: AIConfigPublic


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AdminTokenResponse(BaseModel):
    access_token: str
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    nightly_activity_enabled: bool = True


class UserUpdate(BaseModel):
    nightly_activity_enabled: bool | None = None
    regenerate_token: bool = False
    base_url: str | None = Field(default=None, min_length=1, max_length=255)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nightly_activity_enabled: bool
    is_active: bool
    created_at: datetime
    activation_code: str | None = None


class UserListResponse(BaseModel):
    items: list[UserResponse]
