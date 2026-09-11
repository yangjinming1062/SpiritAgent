from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelCapabilities(BaseModel):
    """注册表里某渠道的静态能力位，随 GET /api/channels 一起返回供 UI 折叠不支持的开关。"""

    supports_typing: bool = False
    supports_media: bool = False
    can_initiate: bool = False
    requires_login: bool = False


class BindingInfo(BaseModel):
    """绑定状态视图；凭据与渠道 config 一律不回显（config 是 PUT 入参，读侧无出口）。"""

    model_config = ConfigDict(from_attributes=True)

    status: str
    account_ref: str = ""
    account_name: str = ""
    conversation_id: int | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class ChannelInfo(BaseModel):
    channel: str
    title: str
    capabilities: ChannelCapabilities
    binding: BindingInfo | None = None


class ChannelListResponse(BaseModel):
    items: list[ChannelInfo]


class ChannelBindingPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict = Field(default_factory=dict)


class PeerInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    peer_id: str
    peer_name: str = ""
    status: Literal["pending", "allowed", "blocked"]
    last_message_at: datetime | None = None


class PeerListResponse(BaseModel):
    items: list[PeerInfo]


class PeerActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "block", "delete"]


class ChannelLoginStateResponse(BaseModel):
    """扫码登录轮询视图：state ∈ idle|wait|scaned|confirmed|expired|error|login_required|connected；
    qr_image 为渠道下发的二维码图内容（图片 URL 或 data URL），confirmed 后不再返回。
    """

    state: str
    qr_image: str | None = None
    error: str | None = None
