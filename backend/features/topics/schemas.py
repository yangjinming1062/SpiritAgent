"""采集恢复、聊天来源与独立反馈的公开值。"""

from datetime import date, datetime
from typing import Any, Literal

from infrastructure.api_ids import ResourceId
from infrastructure.web.social_content import SocialContent, SocialPlatform
from pydantic import BaseModel, ConfigDict, Field

TopicFeedbackValue = Literal["like", "less"]


class TopicBatchSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: ResourceId
    activity_date: date
    started_at: datetime
    window_start: datetime
    window_end: datetime
    available_from: datetime
    expires_at: datetime
    status: Literal["collecting", "ready", "failed"]
    search_requests: int
    detail_requests: int
    checkpoint: dict[str, Any]
    error: str | None


class TopicSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: SocialContent
    summary: str = Field(min_length=1, max_length=2500)
    user_relevance: str = Field(max_length=1200)
    companion_interest: str = Field(max_length=1200)
    conversation_angle: str = Field(min_length=1, max_length=1200)
    event_key: str = Field(min_length=1, max_length=200)


class TopicCardSnapshot(BaseModel):
    type: Literal["link"] = "link"
    topic_id: ResourceId
    title: str
    platform: SocialPlatform
    url: str
    published_at: datetime
    feedback: TopicFeedbackValue | None = None


class TopicSnapshot(TopicCardSnapshot):
    source_id: str
    author: str
    body: str
    subtitle: str
    collected_at: datetime
    content_type: Literal["text", "image", "video"]
    summary: str
    user_relevance: str
    companion_interest: str
    conversation_angle: str
    event_key: str
    is_used: bool


class TopicFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: TopicFeedbackValue | None


class TopicFeedbackSnapshot(BaseModel):
    topic_id: ResourceId
    value: TopicFeedbackValue | None
    updated_at: datetime | None
