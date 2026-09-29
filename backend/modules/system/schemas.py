from typing import Any, Literal

from pydantic import BaseModel, Field

from ..auth import ChatRequestClientContext


class MessageResponse(BaseModel):
    message: str


class DesktopConfigResponse(BaseModel):
    config: dict


class DesktopConfigPutRequest(BaseModel):
    config: dict


class CompletionResponse(BaseModel):
    content: str
    usage: dict[str, Any] | None = None


class ReleaseManifestFileItem(BaseModel):
    url: str
    sha512: str
    size: int


class ReleaseManifestResponse(BaseModel):
    version: str
    releaseDate: str
    releaseNotes: str = ""
    path: str
    sha512: str
    files: list[ReleaseManifestFileItem] = Field(default_factory=list)


class ChatMessageRequest(BaseModel):
    """本轮用户输入。"""

    content: str
    attachments: list[dict] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    client_context: ChatRequestClientContext | None = None
    response_preference: Literal["text", "voice"] | None = None


class PromptPresetSummary(BaseModel):
    """``system.list_presets`` RPC 返回的精简元数据：不含 body（预设体永不下发到客户端）。"""

    id: str
    name: str
    description: str = ""
    icon_key: str


class PromptPresetListResponse(BaseModel):
    presets: list[PromptPresetSummary]
