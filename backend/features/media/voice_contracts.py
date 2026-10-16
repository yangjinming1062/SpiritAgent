from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


class VoiceSampleResponse(BaseModel):
    sample_id: str
    url: str
    duration: float
    mime_type: str = "audio/wav"


class CustomVoiceSummary(BaseModel):
    candidate_id: str
    kind: Literal["design", "clone"]
    voice_id: str
    prompt: str
    preview_audio_url: str
    reference_audio_url: str | None
    provider: str
    model: str
    version: int
    available: bool


class VoicePreviewResponse(BaseModel):
    candidate_id: str
    voice_id: str
    provider: str
    trial_audio_base64: str
    trial_audio_mime: str
    expected_version: int
    kind: Literal["design", "clone"]
    prompt: str


class VoiceConfirmResponse(BaseModel):
    voice_id: str
    version: int


@dataclass(frozen=True)
class CustomVoiceSnapshot:
    voice_id: str
    provider: str
    model: str
    model_card_id: str
    provider_voice_id: str
    prompt: str
    reference_path: str | None


class VoicePromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    provider: str
