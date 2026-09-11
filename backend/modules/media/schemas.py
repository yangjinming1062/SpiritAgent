from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class SpeechCue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: str = Field(min_length=1, max_length=80)
    tag: str = Field(min_length=1, max_length=80, pattern=r"^[^\[\]()<>\r\n]+$")


class SpeechPause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before: str = Field(min_length=1, max_length=80)
    seconds: float = Field(ge=0.01, le=99.99, multiple_of=0.01)


class SpeechDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=500)
    scene: str = Field(min_length=1, max_length=500)
    guidance: str = Field(min_length=1, max_length=1500)


class MiMoSpeechStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["mimo"]
    model: str = Field(min_length=1, max_length=100)
    styles: list[Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[^\[\]()<>\r\n]+$")]] = Field(
        default_factory=list,
        max_length=16,
    )
    direction: SpeechDirection
    cues: list[SpeechCue] = Field(default_factory=list, max_length=32)


class MiniMaxSpeechStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["minimax"]
    model: str = Field(min_length=1, max_length=100)
    emotion: (
        Literal["happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent", "whisper"] | None
    ) = None
    speed: float = Field(default=1, ge=0.5, le=2)
    cues: list[SpeechCue] = Field(default_factory=list, max_length=32)
    pauses: list[SpeechPause] = Field(default_factory=list, max_length=32)


SpeechStyle = Annotated[MiMoSpeechStyle | MiniMaxSpeechStyle, Field(discriminator="provider")]
SPEECH_STYLE_ADAPTER = TypeAdapter(SpeechStyle)
