"""Pydantic 契约：伙伴房间图 REST 接口。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .room_backdrop import BackdropIntent, BackdropOrigin, BackdropPolicy, BackdropSource, BackdropStatus


class BackdropResponse(BaseModel):
    id: int
    status: BackdropStatus
    origin: BackdropOrigin
    intent: BackdropIntent
    source: BackdropSource = BackdropSource.GENERATED
    brief: str = ""
    prompt: str = ""
    url: str = ""
    outfit_fingerprint: str = ""
    seed_portrait_media_id: str = ""
    seed_outfit_media_id: str = ""
    error_utterance: str | None = None
    attempt_count: int = 0
    requested_at: datetime | None = None
    ready_at: datetime | None = None


class BackdropListResponse(BaseModel):
    backdrops: list[BackdropResponse]


class RoomStateResponse(BaseModel):
    active: BackdropResponse | None = None
    history: list[BackdropResponse] = Field(default_factory=list)
    policy: BackdropPolicy = BackdropPolicy.LLM_MAY_REPLACE
    pending: BackdropResponse | None = None


class RoomGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: BackdropIntent = BackdropIntent.REBUILD
    notes: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, min_length=1, max_length=8 * 1024 * 1024)
    content_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"] = "image/png"


# 自备图提示词请求：不收场景参考图——提示词是纯文本，用户可自行决定是否向外部工具附参考图。
class RoomPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: BackdropIntent = BackdropIntent.REBUILD
    notes: str | None = Field(default=None, max_length=500)


class RoomActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backdrop_id: int


class BackdropPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: BackdropPolicy


class BackdropPolicyResponse(BaseModel):
    policy: BackdropPolicy
