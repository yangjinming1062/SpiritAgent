from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from modules.media import SPEECH_STYLE_ADAPTER, SpeechCue, SpeechDirection, SpeechPause, SpeechStyle


class SpeechPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: SpeechDirection | None = None
    styles: list[str] | None = None
    emotion: str | None = None
    speed: float | None = None
    cues: list[SpeechCue] | None = None
    pauses: list[SpeechPause] | None = None

    def bind(self, provider: str, model: str) -> SpeechStyle:
        performance = self.model_dump(exclude_unset=True)
        inactive = {"direction", "styles"} if provider == "minimax" else {"emotion", "speed", "pauses"}
        # 通用输入中的空占位不表达演绎；非空的跨供应商字段仍由严格模型拒绝。
        for name in inactive:
            if performance.get(name) in (None, []):
                performance.pop(name, None)
        return SPEECH_STYLE_ADAPTER.validate_python({**performance, "provider": provider, "model": model})


class TextBubble(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["text"]
    text: str = Field(min_length=1, max_length=16000)


class VoiceBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=4000)
    speech: SpeechPerformance


class MediaBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["image", "video"]
    media_id: str = Field(min_length=1, max_length=128)


class MediaBubble(MediaBubbleInput):
    status: Literal["pending", "ready", "failed", "result_unknown"]
    url: str | None = Field(default=None, pattern=r"^companion-assets/\d+/[A-Za-z0-9._-]+$")
    error: str | None = None
    # 服务端绑定，不下发模型也不由客户端决定归属。
    goal_id: str = Field(min_length=1, max_length=128)
    job_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_asset_state(self) -> "MediaBubble":
        if (self.status == "ready") != bool(self.url):
            raise ValueError("Ready media requires an asset; unfinished media cannot bind one")
        if self.type == "image" and self.job_id is not None:
            raise ValueError("Images cannot bind a video task")
        return self


class CompanionReplyInput(RootModel):
    root: list[Annotated[TextBubble | VoiceBubbleInput | MediaBubbleInput, Field(discriminator="type")]]


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

    bubbles: list[Annotated[TextBubble | VoiceBubble | MediaBubble, Field(discriminator="type")]] = Field(
        min_length=1,
    )

    def validate_content(self, content: str) -> None:
        """原文与交付态须对应同一组气泡；供应商绑定与音频仅存在于交付态。"""
        source = CompanionReplyInput.model_validate_json(content)
        if len(source.root) != len(self.bubbles):
            raise ValueError("Reply content and delivery bubbles differ")
        for raw, delivered in zip(source.root, self.bubbles, strict=True):
            if isinstance(raw, MediaBubbleInput):
                if not isinstance(delivered, MediaBubble) or (raw.type, raw.media_id) != (
                    delivered.type,
                    delivered.media_id,
                ):
                    raise ValueError("Reply content and delivery media differ")
                continue
            if isinstance(delivered, MediaBubble):
                raise ValueError("Reply content and delivery bubble types differ")
            if raw.type != delivered.type or raw.text != delivered.text:
                raise ValueError("Reply content and delivery dialogue differ")
            if isinstance(raw, VoiceBubbleInput) and isinstance(delivered, VoiceBubble):
                style = raw.speech.bind(delivered.speech.provider, delivered.speech.model)
                if style != delivered.speech:
                    raise ValueError("Reply content and delivery performance differ")
