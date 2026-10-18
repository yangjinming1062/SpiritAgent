import json
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from infrastructure.ai.speech_contracts import SPEECH_STYLE_ADAPTER, SpeechSegment, SpeechStyle
from infrastructure.assets.paths import parse_companion_asset_path
from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator, model_validator


class SpeechPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str | None = None
    styles: list[str] | None = None
    emotion: str | None = None
    speed: float | None = None
    segments: list[SpeechSegment] | None = None

    def bind(self, provider: str, model: str) -> SpeechStyle:
        performance = self.model_dump(exclude_unset=True)
        inactive = {"instruction", "styles"} if provider == "minimax" else {"emotion", "speed"}
        # 通用输入中的空占位不表达演绎；非空的跨供应商字段仍由严格模型拒绝。
        for name in inactive:
            if performance.get(name) in (None, []):
                performance.pop(name, None)
        return SPEECH_STYLE_ADAPTER.validate_python({**performance, "provider": provider, "model": model})


class TextBubble(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["text"]
    text: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=1, max_length=16000)

    @field_validator("text")
    @classmethod
    def validate_text_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value


class VoiceBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["voice"]
    text: str = Field(min_length=1, max_length=4000)
    speech: SpeechPerformance


class MediaBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["image", "video"]
    media_id: str = Field(min_length=1, max_length=128)


class LinkBubbleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["link"]
    topic_id: int = Field(gt=0, strict=True)


class LinkBubble(LinkBubbleInput):
    title: str = Field(min_length=1)
    platform: Literal["x", "xiaohongshu", "douyin"]
    url: str = Field(min_length=1)
    published_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> "LinkBubble":
        hosts = {
            "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
            "xiaohongshu": {"www.xiaohongshu.com", "xiaohongshu.com"},
            "douyin": {"www.douyin.com", "douyin.com"},
        }
        url = urlsplit(self.url)
        if url.scheme != "https" or url.hostname not in hosts[self.platform] or url.username or url.password:
            raise ValueError("A shared source must use its platform's HTTPS URL")
        if url.port not in (None, 443):
            raise ValueError("A source link cannot specify a non-HTTPS port")
        return self

    @field_validator("published_at")
    @classmethod
    def validate_publication_time(cls, value: str) -> str:
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("A source publication time requires a timezone")
        return value


class MediaBubble(MediaBubbleInput):
    status: Literal["pending", "ready", "failed", "result_unknown"]
    url: str | None = None
    error: str | None = None
    # 服务端绑定，不下发模型也不由客户端决定归属。
    goal_id: str = Field(min_length=1, max_length=128)
    job_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_asset_state(self) -> "MediaBubble":
        if self.url is not None and parse_companion_asset_path(self.url) is None:
            raise ValueError("Invalid media asset path")
        if (self.status == "ready") != bool(self.url):
            raise ValueError("Ready media requires an asset; unfinished media cannot bind one")
        if self.type == "image" and self.job_id is not None:
            raise ValueError("Images cannot bind a video task")
        return self


class CompanionReplyInput(RootModel):
    root: list[
        Annotated[TextBubble | VoiceBubbleInput | MediaBubbleInput | LinkBubbleInput, Field(discriminator="type")]
    ]


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

    # 服务端冻结首次语音制作的本地日期；不进入模型输入或客户端气泡。
    audio_directory: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    # 来源快照由服务端冻结；模型等待跨午夜后仍可提交已获准的当前回复。
    topic_context_at: datetime | None = None
    used_topic_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_topic_references(self) -> "CompanionReply":
        if len(set(self.used_topic_ids)) != len(self.used_topic_ids):
            raise ValueError("Topic references must be unique")
        if any(b.topic_id not in self.used_topic_ids for b in self.bubbles if isinstance(b, LinkBubble)):
            raise ValueError("A shared source must be recorded as a used topic")
        return self

    bubbles: list[Annotated[TextBubble | VoiceBubble | MediaBubble | LinkBubble, Field(discriminator="type")]] = Field(
        min_length=1,
    )

    @field_validator("audio_directory")
    @classmethod
    def validate_audio_directory(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @classmethod
    def from_saved_snapshot(cls, reply_json: str, content: str) -> "CompanionReply":
        """补回已存快照丢失的首尾空白；原文仅因这些空白超限时保留合法快照正文。"""
        reply = cls.model_validate_json(reply_json)
        values = json.loads(content)
        comparison_content = content
        if isinstance(values, list) and len(values) == len(reply.bubbles):
            shortened = False
            for raw, delivered in zip(values, reply.bubbles, strict=True):
                if (
                    isinstance(raw, dict)
                    and raw.get("type") == "text"
                    and isinstance(raw.get("text"), str)
                    and len(raw["text"]) > 16000
                    and isinstance(delivered, TextBubble)
                    and raw["text"].strip() == delivered.text
                ):
                    raw["text"] = delivered.text
                    shortened = True
            if shortened:
                comparison_content = json.dumps(values, ensure_ascii=False)
        source = CompanionReplyInput.model_validate_json(comparison_content)
        if len(source.root) == len(reply.bubbles):
            for raw, delivered in zip(source.root, reply.bubbles, strict=True):
                if (
                    isinstance(raw, TextBubble)
                    and isinstance(delivered, TextBubble)
                    and raw.text != delivered.text
                    and raw.text.strip() == delivered.text
                ):
                    delivered.text = raw.text
        reply.validate_content(comparison_content)
        return reply

    def validate_content(self, content: str) -> None:
        """原文与交付态须对应同一组气泡；供应商绑定与音频仅存在于交付态。"""
        source = CompanionReplyInput.model_validate_json(content)
        if len(source.root) != len(self.bubbles):
            raise ValueError("Reply content and delivery bubbles differ")
        for raw, delivered in zip(source.root, self.bubbles, strict=True):
            if isinstance(raw, LinkBubbleInput):
                if not isinstance(delivered, LinkBubble) or raw.topic_id != delivered.topic_id:
                    raise ValueError("Reply content and delivery links differ")
                continue
            if isinstance(raw, MediaBubbleInput):
                if not isinstance(delivered, MediaBubble) or (raw.type, raw.media_id) != (
                    delivered.type,
                    delivered.media_id,
                ):
                    raise ValueError("Reply content and delivery media differ")
                continue
            if isinstance(delivered, (MediaBubble, LinkBubble)):
                raise ValueError("Reply content and delivery bubble types differ")
            if raw.type != delivered.type or raw.text != delivered.text:
                raise ValueError("Reply content and delivery dialogue differ")
            if isinstance(raw, VoiceBubbleInput) and isinstance(delivered, VoiceBubble):
                style = raw.speech.bind(delivered.speech.provider, delivered.speech.model)
                if style != delivered.speech:
                    raise ValueError("Reply content and delivery performance differ")
