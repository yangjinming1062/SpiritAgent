from components import TITLE_MAX_CHARS
from pydantic import BaseModel, ConfigDict, Field


class DesktopSessionInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str = "standard"
    title: str | None = None
    started_at: int
    last_active: int
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    model: str | None = None
    source: str | None = None
    preview: str | None = None
    pinned: bool = False
    archived: bool = False
    is_active: bool = True
    cwd: str | None = None
    ended_at: int | None = None
    lineage_root_id: str | None = Field(default=None, alias="_lineage_root_id", serialization_alias="_lineage_root_id")
    # NULL = 用户普通对话，chat 时按 resolve_preset 降级到 companion。
    system_preset_id: str | None = None
    # 已对 NULL 降级为 companion.icon_key，避免客户端再解析一次。枚举变更需同步 BUILTIN_PRESETS。
    system_preset_icon_key: str | None = None


class DesktopSessionListResponse(BaseModel):
    limit: int
    offset: int
    total: int
    sessions: list[DesktopSessionInfo]


class DesktopSessionSearchResponse(BaseModel):
    sessions: list[DesktopSessionInfo]


class DesktopSessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[dict]
    next_cursor: str | None = None


class DesktopSessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    pinned: bool | None = None
    archived: bool | None = None


class DesktopSessionForkRequest(BaseModel):
    """`POST /api/sessions/{id}/fork` 的请求体：指定源消息 id，从该消息（含）之前派生新会话。"""

    source_message_id: int = Field(ge=0)


class DesktopSessionUndoRequest(BaseModel):
    """`POST /api/sessions/{id}/undo-to-message` 的请求体：指定锚点消息 id，硬删除 ``Message.id >= source_message_id`` 的全部行（含锚点本身）。需要 ``confirmed=true``。"""

    source_message_id: int = Field(ge=0)
    confirmed: bool = False


class DesktopSessionOperationResponse(BaseModel):
    ok: bool = True


class DesktopSessionForkResponse(BaseModel):
    session_id: str
    messages: list[dict]
    message_count: int


class DesktopSessionUndoResponse(BaseModel):
    session_id: str
    deleted_count: int
    anchor: dict
    messages: list[dict]
