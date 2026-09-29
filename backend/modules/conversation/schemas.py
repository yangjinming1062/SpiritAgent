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
    model: str | None = None
    source: str | None = None
    preview: str | None = None
    pinned: bool = False
    archived: bool = False
    is_active: bool = True
    cwd: str | None = None
    ended_at: int | None = None
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


class DesktopSessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    pinned: bool | None = None
    archived: bool | None = None


class DesktopSessionOperationResponse(BaseModel):
    ok: bool = True
