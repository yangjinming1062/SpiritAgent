"""角色特征、局部编辑和分析请求的公共契约。"""

from typing import Literal

from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field

from features.media.identity_contracts import CharacterCardSnapshot, CharacterFeatures, CharacterOverrides

ExtractionStatus = Literal["pending", "running", "ready", "failed"]


class CharacterCardResponse(CharacterCardSnapshot):
    avatar_id: ResourceId
    automatic: CharacterFeatures
    status: ExtractionStatus
    portrait_status: ExtractionStatus
    body_status: ExtractionStatus
    error: str | None


class CharacterCardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: ResourceId = Field()
    expected_revision: int = Field(ge=0)
    changes: CharacterOverrides


class CharacterCardExtract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: ResourceId = Field()
    expected_revision: int = Field(ge=0)
