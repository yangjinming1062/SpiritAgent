from typing import Literal

from infrastructure.constants import TITLE_MAX_CHARS
from pydantic import BaseModel, ConfigDict, Field
from runtime.conversation import SessionSettingsPatch

from features.conversation.client_contracts import SessionMessageView


class DesktopSessionInfo(BaseModel):
    id: str
    kind: str
    title: str | None
    started_at: int
    last_active: int
    message_count: int
    input_tokens: int
    output_tokens: int
    tool_call_count: int
    preview: str | None
    pinned: bool
    archived: bool
    system_preset_id: str | None
    # 服务端按预设目录解析的图标键（自动化会话为 task），客户端直接使用。
    system_preset_icon_key: str | None


class DesktopSessionListResponse(BaseModel):
    limit: int
    offset: int
    total: int
    sessions: list[DesktopSessionInfo]


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_preset_id: str


class SessionForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_message_id: int = Field(ge=0)


class SessionHistoryResponse(BaseModel):
    messages: list[SessionMessageView]
    next_cursor: int | None
    has_more: bool


class DesktopSessionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    pinned: bool | None = None
    archived: bool | None = None
    settings: SessionSettingsPatch | None = None


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
    messages: list[SessionMessageView]
