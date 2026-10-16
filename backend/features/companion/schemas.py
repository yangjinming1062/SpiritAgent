from typing import Literal

from infrastructure.ai.voice_types import VoiceEntry
from pydantic import BaseModel, ConfigDict, Field

from features.media.voice_contracts import CustomVoiceSummary

_PERSONA_JSON_MAX_LEN: int = 32 * 1024
PersonaEditableField = Literal["name", "personality", "relationship", "speaking_style"]


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_json: str = Field(min_length=1, max_length=_PERSONA_JSON_MAX_LEN)
    expected_fields: dict[PersonaEditableField, str | None] | None = None


class PersonaResponse(BaseModel):
    definition_json: str
    is_complete: bool
    personality_tags: list[str] = Field(default_factory=list)
    current_mood: str | None = None


class PortraitConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_avatar_id: int = Field(gt=0)


class VoicesListResponse(BaseModel):
    providers: list[str]
    voices: list[VoiceEntry]
    supports_voice_design: bool
    voice_design_guide: str
    supports_voice_prompt_generation: bool = False
    supports_voice_clone: bool = False
    custom_voices: list[CustomVoiceSummary] = Field(default_factory=list)


class VoiceMatchResponse(BaseModel):
    voice: VoiceEntry | None
    alternatives: list[VoiceEntry]


class OnboardingStateResponse(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    next_field: str | None = None
    complete: bool = False


class CompanionOperationResponse(BaseModel):
    ok: bool = True
