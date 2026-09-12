from typing import Any, Literal

from components import DEFAULT_LANGUAGE
from pydantic import BaseModel, Field

from ..auth import ChatRequestClientContext


class StatusResponse(BaseModel):
    """GET /api/status 返回的每用户后端状态快照：login_count 计数当前用户 active LoginRecord（单设备登录 → 0 或 1）；chat_count 计数 settings.chat_active_window_minutes（默认 30 分钟）内更新过的 Conversation 行；connection_state 反映当前用户聊天 WebSocket 是否被 ConnectionManager 持有。"""

    login_count: int
    chat_count: int
    connection_state: Literal["connected", "disconnected"]


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
    role: str = Field(pattern="^(user|tool)$")
    content: str
    tool_call_id: str | None = None  # role == "tool" 时必填
    attachments: list[dict] | None = None


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^\d+$")
    message: ChatMessageRequest
    model: str | None = None  # 可选覆盖；与 context_tokens 一起使用，覆盖模型的窗口与供应商默认不同时
    context_tokens: int | None = Field(default=None, gt=0)
    client_context: ChatRequestClientContext | None = None
    tools: list[dict] | None = None


class AgentPromptConfig(BaseModel):
    valid_tool_names: list[str] = Field(default_factory=list)
    model: str | None = None
    tools: list[dict] | None = None
    client_context: ChatRequestClientContext | None = None
    identity_prompt: str | None = None
    platform: str = "desktop"
    tool_use_enforcement: str = "auto"
    persona_extras: str | None = None
    user_profile_extras: str | None = None
    # 当前穿着的着装描述（2D 换装）；精灵自知穿着，为着装联动打底
    outfit_extras: str = ""
    background_memory_extras: str = ""
    proactive_memory_extras: str = ""
    language: str = DEFAULT_LANGUAGE
    # 用户本地 IANA tz（如 "Asia/Shanghai"）；用于 volatile header 日期与陪伴对话时间提示走本地时区。
    # None 表示用户未设置，回落到服务端 UTC。
    user_local_tz: str | None = None


class PromptPreset(BaseModel):
    """内置系统提示词预设（不可变，目前为代码常量）。预设体含 ``{{BLOCK}}`` 占位符，运行期由 ``prompt_blocks`` 的 renderer 注册表解析；strict 解析，未识别占位符记录 warning 并保留原文。"""

    id: str
    name: str
    description: str = ""
    icon_key: str
    body: str = Field(min_length=1, max_length=8192)


class PromptPresetSummary(BaseModel):
    """``system.list_presets`` RPC 返回的精简元数据：不含 body（预设体永不下发到客户端）。"""

    id: str
    name: str
    description: str = ""
    icon_key: str


class PromptPresetListResponse(BaseModel):
    presets: list[PromptPresetSummary]
