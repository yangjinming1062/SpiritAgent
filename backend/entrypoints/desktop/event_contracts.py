"""面向客户端的事件载荷；内部任务事件不属于网关协议。"""

from typing import Literal

from features.actions.schemas import ActionPlayCommand
from features.conversation.client_contracts import (
    ClientMediaBubble,
    ClientMediaEntry,
    ReplyBubbleView,
    SessionMessageView,
)
from features.conversation.replies import VoiceBubbleView
from features.desktop_life.schemas import DesktopVideoPlayCommand
from features.journal.schemas import DiaryEntryResponse
from features.media.video_queue.contracts import VideoQueueInfo
from features.posts.schemas import PostCommentResponse, PostResponse
from features.topics.schemas import TopicFeedbackSnapshot
from pydantic import BaseModel, JsonValue

from entrypoints.desktop.contracts import CommandDispatchResult, EmptyResult


class TurnEvent(BaseModel):
    request_id: str | None = None
    origin_kind: str | None = None
    replace_reply_message_id: int | None = None


class MessageDeltaEvent(TurnEvent):
    text: str


class ToolProgressEvent(TurnEvent):
    name: str | None
    call_id: str | None
    status: Literal["running", "complete"]


class MessagePersistedEvent(TurnEvent):
    role: str
    message_ids: list[int]
    messages: list[SessionMessageView]


class MessageBubbleEvent(TurnEvent):
    message_id: int
    bubble_index: int
    bubble: ReplyBubbleView


class MessageMediaEvent(MessageBubbleEvent):
    bubble: ClientMediaBubble
    session_id: str
    media_id: str


class MessageVoiceEvent(MessageBubbleEvent):
    bubble: VoiceBubbleView
    session_id: str


class MessageAudioEvent(BaseModel):
    session_id: str
    message_id: int
    audio_url: str
    duration: float | None = None
    transcript: str | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    context_window: int | None = None


class CacheUsage(BaseModel):
    request_count: int
    reported_request_count: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_hit_rate: float | None


class MessageCompleteEvent(TurnEvent):
    text: str | None = None
    bubbles: list[ReplyBubbleView] | None = None
    reasoning: str | None = None
    media: list[ClientMediaEntry] | None = None
    usage: TokenUsage | None = None
    cache_usage: CacheUsage | None = None
    message_id: int | None = None
    can_replace_reply: bool


class MessageReplacedEvent(TurnEvent):
    session_id: str
    replaced_message_id: int
    message: SessionMessageView
    history_revision: int


class MessageEditedEvent(BaseModel):
    session_id: str
    messages: list[SessionMessageView]


class MessageDeletedEvent(MessageEditedEvent):
    deleted_count: int
    anchor: dict[str, JsonValue]


class CompressionEvent(TurnEvent):
    session_id: str | None = None
    subtype: str
    text: str
    message_id: int | None = None


class GatewayErrorEvent(TurnEvent):
    message: str
    retry_message_id: int | None = None


class SessionStateEvent(TurnEvent):
    messages: list[SessionMessageView] | None = None
    running: bool
    status: str
    error: str | None
    result_message_id: int | None = None


class SessionListChangedEvent(BaseModel):
    session_id: str
    deleted: bool = False


class MemoryChangedEvent(BaseModel):
    system_preset_id: str
    memory_id: int | None = None


class ToolScope(BaseModel):
    user_id: int
    system_preset_id: str


class ToolCallEvent(BaseModel):
    call_id: str
    name: str
    args: dict[str, JsonValue]
    session_id: str | None
    headless: bool
    skill_scope: ToolScope | None


class ToolCancelEvent(BaseModel):
    call_id: str


class SystemNotificationEvent(BaseModel):
    kind: Literal["error", "success"]
    title: str
    message: str
    session_id: str


class CompanionMessageEvent(BaseModel):
    session_id: str
    message_id: int
    bubbles: list[ReplyBubbleView]


class MoodEvent(BaseModel):
    mood: str


class VoiceUpdatedEvent(BaseModel):
    voice_id: str
    version: int


class SceneChangedEvent(BaseModel):
    scene_id: int | None = None
    version: int
    switch_version: int


class OutfitChangedEvent(BaseModel):
    outfit_id: int
    worn: bool


class PackEvent(BaseModel):
    packId: int
    outfitId: int | None


class PackFailedEvent(PackEvent):
    reason: str


class PackProgressEvent(PackEvent):
    stage: str
    action: str | None = None
    video_queue: VideoQueueInfo | None = None


class ActionJobEvent(PackEvent):
    actionId: int | None = None
    action: str
    stage: str


class ActionCatalogEvent(BaseModel):
    packId: int
    catalogVersion: int
    appearanceEpoch: int


class CharacterCardEvent(BaseModel):
    avatar_id: int
    revision: int
    status: Literal["pending", "running", "ready", "failed"]


class DesktopVideoUpdatedEvent(BaseModel):
    version: int


class VideoQueueEvent(BaseModel):
    task_id: str
    session_id: str | None
    media_id: str | None
    video_queue: VideoQueueInfo | None


class VideoCompletedEvent(BaseModel):
    task_id: str
    session_id: str | None
    url: str
    media: list[ClientMediaEntry]
    warning: str | None = None


class VideoFailedEvent(BaseModel):
    task_id: str
    session_id: str | None
    error: str | None
    status_message_id: int | None
    status_text: str | None


class PostCommentEvent(BaseModel):
    post_id: str
    comment: PostCommentResponse


class PostCommentDeletedEvent(BaseModel):
    post_id: str
    comment_id: str


class DiaryDeletedEvent(BaseModel):
    diary_id: str
    entry_date: str


EVENT_CONTRACTS: dict[str, type[BaseModel]] = {
    "message.delta": MessageDeltaEvent,
    "message.reasoning.delta": MessageDeltaEvent,
    "message.start": TurnEvent,
    "message.break": TurnEvent,
    "message.bubble": MessageBubbleEvent,
    "message.complete": MessageCompleteEvent,
    "message.replaced": MessageReplacedEvent,
    "message.persisted": MessagePersistedEvent,
    "message.edited": MessageEditedEvent,
    "message.deleted": MessageDeletedEvent,
    "message.audio": MessageAudioEvent,
    "message.voice": MessageVoiceEvent,
    "message.media": MessageMediaEvent,
    "tool.start": ToolProgressEvent,
    "tool.complete": ToolProgressEvent,
    "tool.call": ToolCallEvent,
    "tool.cancel": ToolCancelEvent,
    "session.state": SessionStateEvent,
    "session.list_changed": SessionListChangedEvent,
    "command.result": CommandDispatchResult,
    "compress.completed": CompressionEvent,
    "error": GatewayErrorEvent,
    "system.notification": SystemNotificationEvent,
    "memory.changed": MemoryChangedEvent,
    "companion.message": CompanionMessageEvent,
    "companion.mood": MoodEvent,
    "companion.voice.updated": VoiceUpdatedEvent,
    "companion.scene.updated": SceneChangedEvent,
    "companion.scene.activated": SceneChangedEvent,
    "companion.outfit.updated": OutfitChangedEvent,
    "companion.video.ready": PackEvent,
    "companion.video.activated": PackEvent,
    "companion.video.failed": PackFailedEvent,
    "companion.video.progress": PackProgressEvent,
    "companion.action.job_updated": ActionJobEvent,
    "companion.action.catalog_changed": ActionCatalogEvent,
    "companion.action.play_requested": ActionPlayCommand,
    "companion.character_card.updated": CharacterCardEvent,
    "companion.desktop_video.updated": DesktopVideoUpdatedEvent,
    "companion.desktop_video.play_requested": DesktopVideoPlayCommand,
    "video_gen.queue": VideoQueueEvent,
    "video_gen.completed": VideoCompletedEvent,
    "video_gen.failed": VideoFailedEvent,
    "companion.post.created": PostResponse,
    "companion.post.comment": PostCommentEvent,
    "companion.post.comment.deleted": PostCommentDeletedEvent,
    "companion.posts.read": EmptyResult,
    "companion.diary.created": DiaryEntryResponse,
    "companion.diary.deleted": DiaryDeletedEvent,
    "companion.diary.read": EmptyResult,
    "companion.topic_feedback.updated": TopicFeedbackSnapshot,
}


def validate_gateway_event(event_type: str, payload: object) -> None:
    model = EVENT_CONTRACTS.get(event_type)
    if model is None:
        raise ValueError(f"Unknown gateway event: {event_type}")
    model.model_validate({} if payload is None else payload)
