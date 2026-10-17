"""面向客户端的事件载荷；内部任务事件不属于网关协议。"""

from dataclasses import dataclass
from typing import Literal

from features.actions.schemas import ActionPlayCommand
from features.conversation.client_contracts import (
    ClientMediaBubble,
    ClientMediaEntry,
    ReplyBubbleView,
    SessionMessageView,
)
from features.conversation.http_contracts import CommandDispatchResult
from features.conversation.replies import VoiceBubbleView
from features.conversation.schemas import UndoAnchor
from features.desktop_life.schemas import DesktopVideoPlayCommand
from features.journal.schemas import DiaryEntryResponse
from features.media.video_queue.contracts import VideoQueueInfo
from features.posts.schemas import PostCommentResponse, PostResponse
from features.topics.schemas import TopicFeedbackSnapshot
from pydantic import BaseModel, JsonValue

from entrypoints.desktop.contracts import EmptyResult


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
    anchor: UndoAnchor


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


@dataclass(frozen=True)
class EventContract:
    payload: type[BaseModel]
    audience: Literal["all", "desktop"]
    recovery: Literal["session", "resource", "transient", "device"]
    baseline: bool = False


EVENT_CONTRACTS: dict[str, EventContract] = {
    "message.delta": EventContract(MessageDeltaEvent, "all", "session", baseline=True),
    "message.reasoning.delta": EventContract(MessageDeltaEvent, "all", "session", baseline=True),
    "message.start": EventContract(TurnEvent, "all", "session", baseline=True),
    "message.break": EventContract(TurnEvent, "all", "session", baseline=True),
    "message.bubble": EventContract(MessageBubbleEvent, "all", "session", baseline=True),
    "message.complete": EventContract(MessageCompleteEvent, "all", "session"),
    "message.replaced": EventContract(MessageReplacedEvent, "all", "session"),
    "message.persisted": EventContract(MessagePersistedEvent, "all", "session"),
    "message.edited": EventContract(MessageEditedEvent, "all", "session"),
    "message.deleted": EventContract(MessageDeletedEvent, "all", "session"),
    "message.audio": EventContract(MessageAudioEvent, "all", "session"),
    "message.voice": EventContract(MessageVoiceEvent, "all", "session"),
    "message.media": EventContract(MessageMediaEvent, "all", "session"),
    "tool.start": EventContract(ToolProgressEvent, "all", "session", baseline=True),
    "tool.complete": EventContract(ToolProgressEvent, "all", "session", baseline=True),
    "tool.call": EventContract(ToolCallEvent, "desktop", "device"),
    "tool.cancel": EventContract(ToolCancelEvent, "desktop", "device"),
    "session.state": EventContract(SessionStateEvent, "all", "session"),
    "session.list_changed": EventContract(SessionListChangedEvent, "all", "resource"),
    "command.result": EventContract(CommandDispatchResult, "all", "session"),
    "compress.completed": EventContract(CompressionEvent, "all", "session"),
    "error": EventContract(GatewayErrorEvent, "all", "transient"),
    "system.notification": EventContract(SystemNotificationEvent, "all", "transient"),
    "memory.changed": EventContract(MemoryChangedEvent, "all", "resource"),
    "companion.message": EventContract(CompanionMessageEvent, "all", "transient"),
    "companion.mood": EventContract(MoodEvent, "all", "transient"),
    "companion.voice.updated": EventContract(VoiceUpdatedEvent, "desktop", "resource"),
    "companion.scene.updated": EventContract(SceneChangedEvent, "all", "resource"),
    "companion.scene.activated": EventContract(SceneChangedEvent, "all", "resource"),
    "companion.outfit.updated": EventContract(OutfitChangedEvent, "all", "resource"),
    "companion.video.ready": EventContract(PackEvent, "all", "resource"),
    "companion.video.activated": EventContract(PackEvent, "all", "resource"),
    "companion.video.failed": EventContract(PackFailedEvent, "all", "resource"),
    "companion.video.progress": EventContract(PackProgressEvent, "all", "resource"),
    "companion.action.job_updated": EventContract(ActionJobEvent, "desktop", "resource"),
    "companion.action.catalog_changed": EventContract(ActionCatalogEvent, "desktop", "resource"),
    "companion.action.play_requested": EventContract(ActionPlayCommand, "desktop", "transient"),
    "companion.character_card.updated": EventContract(CharacterCardEvent, "all", "resource"),
    "companion.desktop_video.updated": EventContract(DesktopVideoUpdatedEvent, "desktop", "resource"),
    "companion.desktop_video.play_requested": EventContract(DesktopVideoPlayCommand, "desktop", "transient"),
    "video_gen.queue": EventContract(VideoQueueEvent, "all", "resource"),
    "video_gen.completed": EventContract(VideoCompletedEvent, "all", "resource"),
    "video_gen.failed": EventContract(VideoFailedEvent, "all", "resource"),
    "companion.post.created": EventContract(PostResponse, "all", "resource"),
    "companion.post.comment": EventContract(PostCommentEvent, "all", "resource"),
    "companion.post.comment.deleted": EventContract(PostCommentDeletedEvent, "all", "resource"),
    "companion.posts.read": EventContract(EmptyResult, "all", "resource"),
    "companion.diary.created": EventContract(DiaryEntryResponse, "all", "resource"),
    "companion.diary.deleted": EventContract(DiaryDeletedEvent, "all", "resource"),
    "companion.diary.read": EventContract(EmptyResult, "all", "resource"),
    "companion.topic_feedback.updated": EventContract(TopicFeedbackSnapshot, "all", "resource"),
}


def serialize_gateway_event(event_type: str, payload: object) -> dict[str, JsonValue]:
    contract = EVENT_CONTRACTS.get(event_type)
    if contract is None:
        raise ValueError(f"Unknown gateway event: {event_type}")
    return contract.payload.model_validate({} if payload is None else payload).model_dump(mode="json")


def gateway_event_audience(event_type: str) -> str:
    contract = EVENT_CONTRACTS.get(event_type)
    if contract is None:
        raise ValueError(f"Unknown gateway event: {event_type}")
    return contract.audience
