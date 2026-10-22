"""历史、实时交付和恢复快照共用的客户端消息投影。"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from features.conversation.replies import LinkBubble, TextBubble, VoiceBubbleView
from features.media.video_queue.contracts import VideoQueueInfo


class ClientMediaBubble(BaseModel):
    type: Literal["image", "video"]
    media_id: str
    status: Literal["pending", "ready", "failed", "result_unknown"]
    url: str | None
    error: str | None
    video_queue: VideoQueueInfo | None = None


type ReplyBubbleView = Annotated[
    TextBubble | VoiceBubbleView | ClientMediaBubble | LinkBubble,
    Field(discriminator="type"),
]


class ClientMediaEntry(BaseModel):
    type: Literal["image", "video", "audio"]
    url: str
    audio_url: str | None = None
    duration: float | None = None
    transcript: str | None = None


class SessionMessageFields(BaseModel):
    id: int = 0
    role: Literal["assistant", "system", "tool", "user"]
    content: str | None
    subtype: str = ""
    reasoning: str | None = None
    timestamp: int = 0
    can_replace_reply: bool = False
    media: list[ClientMediaEntry] = Field(default_factory=list)
    tool_calls: JsonValue = None
    tool_call_id: str | None = None
    tool_name: str = ""


class PlainSessionMessageView(SessionMessageFields):
    content_type: Literal["multimodal_v1", "text"]


class CompanionSessionMessageView(SessionMessageFields):
    role: Literal["assistant"]
    content_type: Literal["companion_reply"]
    bubbles: list[ReplyBubbleView]


type SessionMessageView = Annotated[
    PlainSessionMessageView | CompanionSessionMessageView,
    Field(discriminator="content_type"),
]
