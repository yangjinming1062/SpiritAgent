from typing import Any, Literal

from components import TITLE_MAX_CHARS
from pydantic import BaseModel, Field


class DesktopSessionInfo(BaseModel):
    id: str
    kind: str = "standard"
    title: str | None = None
    started_at: int
    last_active: int
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    preview: str | None = None
    pinned: bool = False
    archived: bool = False
    system_preset_id: str | None = None
    # 服务端按预设目录解析的图标键（自动化会话为 task），客户端直接使用。
    system_preset_icon_key: str | None = None


class DesktopSessionListResponse(BaseModel):
    limit: int
    offset: int
    total: int
    sessions: list[DesktopSessionInfo]


class DesktopSessionSearchResponse(BaseModel):
    sessions: list[DesktopSessionInfo]


class DesktopSessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    pinned: bool | None = None
    archived: bool | None = None


class DesktopSessionOperationResponse(BaseModel):
    ok: bool = True


class UndoAttachment(BaseModel):
    type: Literal["image"]
    url: str


class UndoAnchor(BaseModel):
    """撤回后退回输入框的草稿：锚点消息的用户正文与可重新附加的图片，视频随撤回清理、不恢复。"""

    text: str
    attachments: list[UndoAttachment]


class UndoResult(BaseModel):
    session_id: str
    deleted_count: int
    anchor: UndoAnchor
    # 截断后的完整历史，形状见 build_session_messages。
    messages: list[dict[str, Any]]
