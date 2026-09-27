from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from modules.media import SPEECH_STYLE_ADAPTER, SpeechCue, SpeechDirection, SpeechPause, SpeechStyle


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


class CompanionReplyInput(RootModel):
    root: list[Annotated[TextBubble | VoiceBubbleInput, Field(discriminator="type")]] = Field(
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

    def validate_content(self, content: str) -> None:
        """原文与交付态必须对应同一组气泡和演绎；供应商绑定与音频仅存在于交付态。"""
        source = CompanionReplyInput.model_validate_json(content)
        if len(source.root) != len(self.bubbles):
            raise ValueError("Reply content and delivery bubbles differ")
        for raw, delivered in zip(source.root, self.bubbles, strict=True):
            if raw.type != delivered.type or raw.text != delivered.text:
                raise ValueError("Reply content and delivery dialogue differ")
            if isinstance(raw, VoiceBubbleInput) and isinstance(delivered, VoiceBubble):
                style = SPEECH_STYLE_ADAPTER.validate_python(
                    {
                        **raw.speech.model_dump(exclude_unset=True),
                        "provider": delivered.speech.provider,
                        "model": delivered.speech.model,
                    },
                )
                if style != delivered.speech:
                    raise ValueError("Reply content and delivery performance differ")
