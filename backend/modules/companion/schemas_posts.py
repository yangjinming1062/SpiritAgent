"""动态公共接口及独立制作上下文。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .posts import PostCommentRole, PostContentType

ReplyStatus = Literal["none", "pending", "running", "completed", "failed"]
POST_COMMENT_MAX_CHARS = 500


class PostPublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_id: str
    status: Literal[
        "queued",
        "running",
        "published",
        "partial",
        "failed",
        "blocked",
        "result_unknown",
        "declined",
        "discarded",
    ]
    post_id: str | None
    error: str | None


class PostPublicationRecovery(PostPublicationResult):
    title: str
    video_status: Literal["pending", "ready", "failed", "unknown", "discarded"]
    media_url: str | None
    can_adopt: bool
    can_discard: bool


class PostPublicationRecoveryList(BaseModel):
    items: list[PostPublicationRecovery]
    next_offset: int | None


class PostContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_intent: str = ""
    creation_intent: str = ""
    transcript: str = ""
    narration: str = ""
    voice_id: str = ""


class PostPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content_type: PostContentType
    title: str = Field(min_length=1, max_length=64)
    body: str = Field(default="", max_length=500)
    prompt: str = Field(default="", max_length=4000)
    text: str = Field(default="", max_length=800)
    narration: str = Field(default="", max_length=800)
    depicts_self: bool = False
    size: Literal["1024x1024", "1024x1792", "1792x1024", "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"] = (
        "1024x1024"
    )
    duration: Literal[6, 10] = 6
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] = "16:9"

    @model_validator(mode="after")
    def require_content(self) -> "PostPlan":
        if self.content_type == PostContentType.TEXT and not self.body:
            raise ValueError("文字动态需要正文")
        if self.content_type in (PostContentType.IMAGE, PostContentType.VIDEO) and not self.prompt:
            raise ValueError("图片和视频动态需要制作说明")
        if self.content_type == PostContentType.AUDIO and not self.text:
            raise ValueError("语音动态需要语音文稿")
        return self


class PostCommentResponse(BaseModel):
    id: str
    post_id: str
    role: PostCommentRole
    content: str
    created_at: datetime
    updated_at: datetime
    reply_to_comment_id: str | None
    reply_status: ReplyStatus
    reply_error: str | None


class PostResponse(BaseModel):
    id: str
    published_at: datetime
    content_type: PostContentType
    title: str
    body: str
    media_url: str | None
    audio_url: str | None
    comments: list[PostCommentResponse]


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    next_cursor: str | None
    unread_post_ids: list[str]


class PostUnreadResponse(BaseModel):
    has_unread: bool


class PostReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_ids: list[UUID] = Field(min_length=1)


class PostCommentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=POST_COMMENT_MAX_CHARS)
