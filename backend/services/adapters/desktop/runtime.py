import asyncio
from dataclasses import dataclass, field
from typing import Any

from components import safe_json_loads
from modules.conversation import Conversation
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.llm import ReasoningEffort, UserLlmConfig, resolve_context_tokens


class SessionSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    temperature: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    context_compression_threshold: float | None = Field(default=None, ge=0.3, le=1, allow_inf_nan=False)
    reasoning_effort: ReasoningEffort | None = None


class SessionRuntimeInfo(BaseModel):
    model: str | None
    provider: str
    running: bool
    system_preset_id: str
    settings: dict[str, Any] = Field(default_factory=dict)
    context_window: int | None = None
    # 会话权限与语音入口的权威判定源。
    kind: str = "standard"
    is_automation: bool = False


class SessionCreateResult(BaseModel):
    session_id: str
    info: SessionRuntimeInfo


@dataclass(frozen=True)
class ToolSnapshot:
    name: str | None
    call_id: str | None
    status: str


@dataclass
class ActiveTurnSnapshot:
    request_id: str
    origin_kind: str
    message_ids: list[int] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    reasoning: str = ""
    bubbles: list[dict[str, Any]] = field(default_factory=list)
    tools: list[ToolSnapshot] = field(default_factory=list)
    running: bool = True


@dataclass(frozen=True)
class LastSubmissionSnapshot:
    request_id: str
    status: str
    error: str | None
    message_ids: list[int]
    retry_message_id: int | None


class SessionResumeResult(BaseModel):
    session_id: str
    message_count: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    info: SessionRuntimeInfo
    resumed: bool = False
    replayed_count: int = 0
    current_seq: int = 0
    stream_id: str = ""
    active_turn: ActiveTurnSnapshot | None = None
    last_submission: LastSubmissionSnapshot | None = None
    truncated: bool = False
    next_cursor: str | None = None
    # after_id 命中且锚点仍存在时为 True：messages 仅为增量，客户端按 id 合并本地缓存。
    incremental: bool = False


class ToolsSyncResult(BaseModel):
    count: int


@dataclass
class RuntimeSession:
    conversation_id: int
    system_preset_id: str
    chat_task: asyncio.Task | None = None
    settlement_task: asyncio.Task | None = None
    # 设置写回 Conversation，挂载时重新读取。
    settings: dict[str, Any] = field(default_factory=dict)
    # 会话权限不依赖前端当前列表。
    kind: str = "standard"
    is_automation: bool = False
    origin_kind: str = "desktop"
    origin_id: str = ""
    active_turn: ActiveTurnSnapshot | None = None
    snapshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_id(self) -> str:
        """renderer 侧 id（Conversation.id 的一次性字符串化）。"""
        return str(self.conversation_id)

    @property
    def busy(self) -> bool:
        return (
            (self.chat_task is not None and not self.chat_task.done())
            or (self.active_turn is not None and self.active_turn.running)
            or (self.settlement_task is not None and not self.settlement_task.done())
        )


def decode_session_settings(settings_json: str | None) -> dict[str, Any]:
    decoded = safe_json_loads(settings_json or "")
    return decoded if isinstance(decoded, dict) else {}


def new_runtime_session(conv: Conversation) -> RuntimeSession:
    """为已存在的 DB 会话创建 runtime；settings 解码自 Conversation.settings_json，让回合逻辑不必每次回查 DB。"""
    return RuntimeSession(
        conversation_id=conv.id,
        system_preset_id=conv.system_preset_id,
        settings=decode_session_settings(conv.settings_json),
        kind=conv.kind,
        is_automation=conv.is_automation,
    )


def build_runtime_info(
    llm_config: UserLlmConfig,
    runtime: RuntimeSession,
    settings: dict[str, Any],
    *,
    system_preset_id: str,
) -> SessionRuntimeInfo:
    """发给 renderer 的会话运行信息；settings 为会话覆盖叠加生效推理参数。"""
    provider = llm_config.provider_name or "openai"
    return SessionRuntimeInfo(
        model=llm_config.model_name,
        provider=provider,
        running=runtime.busy,
        system_preset_id=system_preset_id,
        settings=settings,
        context_window=resolve_context_tokens(llm_config.config) if llm_config.config else None,
        kind=runtime.kind,
        is_automation=runtime.is_automation,
    )
