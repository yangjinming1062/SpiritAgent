from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PairingResponse(BaseModel):
    id: int
    url: str | None = None
    expires_at: datetime
    state: Literal["pending", "paired", "cancelled", "expired"]


class ExchangeRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    device_name: str = Field(default="手机浏览器", min_length=1, max_length=80)


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    expires_at: datetime
    last_seen_at: datetime
    created_at: datetime


class RemoteUserResponse(BaseModel):
    id: int
    username: str


class RemoteSessionResponse(BaseModel):
    user: RemoteUserResponse
    device: DeviceResponse
    csrf_token: str


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]


class RemoteTicketResponse(BaseModel):
    ticket: str
    expires_in: int
