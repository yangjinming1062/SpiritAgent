from typing import Literal

from pydantic import BaseModel, Field
from runtime.client_context import ChatRequestClientContext


class ChatAttachment(BaseModel):
    type: Literal["image", "video", "audio"]
    file_url: str = Field(min_length=1)


class ChatMessageRequest(BaseModel):
    content: str
    attachments: list[ChatAttachment] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    client_context: ChatRequestClientContext | None = None
    response_preference: Literal["text", "voice"] | None = None


class PromptPresetSummary(BaseModel):
    """预设目录的精简元数据；不含 body（预设体永不下发到客户端）。"""

    id: str
    name: str
    description: str = ""
    icon_key: str


class PromptPresetListResponse(BaseModel):
    presets: list[PromptPresetSummary]
