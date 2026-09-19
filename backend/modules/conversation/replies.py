import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from modules.media import SpeechCue, SpeechDirection, SpeechPause, SpeechStyle


class SpeechPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: SpeechDirection | None = None
    styles: list[str] | None = None
    emotion: str | None = None
    speed: float | None = None
    cues: list[SpeechCue] | None = None
    pauses: list[SpeechPause] | None = None


class TextBubble(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=16000)


class VoiceBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=4000)
    speech: SpeechPerformance


class CompanionReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bubbles: list[Annotated[TextBubble | VoiceBubbleInput, Field(discriminator="type")]] = Field(
        max_length=16,
    )


class ReplyAudio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    duration: float = Field(gt=0, allow_inf_nan=False)


class VoiceBubbleView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["voice"]
    text: str
    audio: ReplyAudio | None


class VoiceBubble(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=4000)
    speech: SpeechStyle
    voice_id: str
    language: str
    audio: ReplyAudio | None = None


class CompanionReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bubbles: list[Annotated[TextBubble | VoiceBubble, Field(discriminator="type")]] = Field(
        min_length=1,
        max_length=16,
    )

    def dialogue(self) -> str:
        return "\n\n".join(bubble.text for bubble in self.bubbles)

    def context_json(self) -> str:
        bubbles = []
        for bubble in self.bubbles:
            item = {"type": bubble.type, "text": bubble.text}
            if isinstance(bubble, VoiceBubble):
                item["speech"] = bubble.speech.model_dump(exclude={"provider", "model"}, exclude_none=True)
            bubbles.append(item)
        return json.dumps({"bubbles": bubbles}, ensure_ascii=False)
