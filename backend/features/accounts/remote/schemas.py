from datetime import datetime
from typing import Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field

REMOTE_TOKEN_MIN_LENGTH = 32
REMOTE_TOKEN_MAX_LENGTH = 128


class PairingResponse(BaseModel):
    id: ResourceId
    url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    expires_at: datetime
    state: Literal["pending", "paired", "cancelled", "expired"]


class ExchangeRequest(BaseModel):
    token: str = Field(min_length=REMOTE_TOKEN_MIN_LENGTH, max_length=REMOTE_TOKEN_MAX_LENGTH)
    device_name: str = Field(default="手机浏览器", min_length=1, max_length=80, pattern=r"^[^\x00]*$")


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    name: str
    expires_at: datetime
    last_seen_at: datetime
    created_at: datetime


class RemoteUserResponse(BaseModel):
    id: ResourceId
    username: str


class RemoteSessionResponse(BaseModel):
    user: RemoteUserResponse
    device: DeviceResponse
    csrf_token: str


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
