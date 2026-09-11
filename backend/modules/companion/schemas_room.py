"""Pydantic 契约：伙伴房间图 REST 接口。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BackdropStatusLiteral = Literal["pending", "ready", "failed", "superseded"]
BackdropOriginLiteral = Literal["onboarding", "outfit", "llm", "nightly", "user_request", "rollback"]
BackdropIntentLiteral = Literal["decorate", "seasonal", "mood", "rebuild"]
BackdropPolicyLiteral = Literal["locked", "llm_may_replace"]


class BackdropResponse(BaseModel):
    id: int
    status: BackdropStatusLiteral
    origin: BackdropOriginLiteral
    intent: BackdropIntentLiteral
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
    policy: BackdropPolicyLiteral = "llm_may_replace"
    pending: BackdropResponse | None = None


class RoomGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: BackdropIntentLiteral = "rebuild"
    notes: str | None = Field(default=None, max_length=500)


class RoomActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backdrop_id: int


class BackdropPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: BackdropPolicyLiteral


class BackdropPolicyResponse(BaseModel):
    policy: BackdropPolicyLiteral
