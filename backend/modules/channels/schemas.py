from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelTurnSource(BaseModel):
    """服务端捕获的对端及授权版本；异步结果不能随来信转投其他对端。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: int = Field(gt=0)
    peer_record_id: int = Field(gt=0)
    peer_id: str = Field(min_length=1, max_length=128)
    authorization_revision: int = Field(ge=0)


class ChannelDeliveryMedia(BaseModel):
    type: Literal["image", "video"]
    url: str


class ChannelDeliveryPayload(BaseModel):
    text: str = ""
    media: list[ChannelDeliveryMedia] = Field(default_factory=list)
    channel_source: ChannelTurnSource | None = None


class BindingInfo(BaseModel):
    """绑定状态视图；凭据一律不回显。"""

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
    binding: BindingInfo | None = None


class ChannelListResponse(BaseModel):
    items: list[ChannelInfo]


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
    """扫码登录轮询视图：unsupported 是无登录流渠道的基类默认值；qr_image 为渠道下发的二维码内容，仅 wait 时返回；error 状态不含原因文本，由客户端显示本地化提示。"""

    state: Literal["unsupported", "wait", "scaned", "confirmed", "expired", "error", "login_required", "connected"]
    qr_image: str | None = None
