"""内容领域的值对象；消费方不接触持久模型。"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal


class PostContentType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class PostCommentRole(StrEnum):
    USER = "user"
    COMPANION = "companion"


class EntryError(RuntimeError):
    pass


class EntryNotFoundError(EntryError):
    pass


@dataclass(frozen=True)
class CommentView:
    id: int
    user_id: int
    post_id: int
    role: str
    content: str
    reply_to_comment_id: int | None
    reply_status: str
    reply_error: str | None
    reply_attempts: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EntryView:
    id: int
    user_id: int
    kind: Literal["post", "diary"]
    activity_date: date
    published_at: datetime
    is_read: bool
    content_type: str
    title: str
    body: str
    context_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    media_url: str | None = None
    audio_url: str | None = None
    comment_count: int = 0
    post_ids: tuple[int, ...] = ()

    @property
    def entry_date(self) -> date:
        return self.activity_date
