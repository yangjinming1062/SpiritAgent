import asyncio
from dataclasses import dataclass, field
from typing import Any

from infrastructure.ai.providers.base import ReasoningEffort
from infrastructure.ai.providers.registry import resolve_context_tokens
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.api_ids import ResourceId
from pydantic import BaseModel, ConfigDict, Field


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
    message_ids: list[ResourceId] = field(default_factory=list)
    assistant_message_id: ResourceId | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    reasoning: str = ""
    bubbles: list[dict[str, Any]] = field(default_factory=list)
    tools: list[ToolSnapshot] = field(default_factory=list)
    running: bool = True
    replace_reply_message_id: ResourceId | None = None


@dataclass(frozen=True)
class LastSubmissionSnapshot:
    request_id: str
    status: str
    error: str | None
    message_ids: list[ResourceId]
    retry_message_id: ResourceId | None
    replace_reply_message_id: ResourceId | None = None
    result_message_id: ResourceId | None = None


class ToolsSyncResult(BaseModel):
    count: int


@dataclass
class RuntimeSession:
    conversation_id: int
    chat_task: asyncio.Task | None = None
    settlement_task: asyncio.Task | None = None
    cancel_requested: bool = False
    # 设置写回 Conversation，挂载时重新读取。
    settings: dict[str, Any] = field(default_factory=dict)
    # 会话权限不依赖前端当前列表。
    kind: str = "standard"
    is_automation: bool = False
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


def new_runtime_session(
    *,
    conversation_id: int,
    settings_json: dict[str, Any] | None,
    kind: str,
    is_automation: bool,
) -> RuntimeSession:
    """为已存在的 DB 会话创建 runtime；settings 复制自 Conversation.settings_json，让回合逻辑不必每次回查 DB。"""
    return RuntimeSession(
        conversation_id=conversation_id,
        settings=dict(settings_json or {}),
        kind=kind,
        is_automation=is_automation,
    )


def build_runtime_info(
    llm_config: UserLlmConfig,
    runtime: RuntimeSession,
    settings: dict[str, Any],
    *,
    system_preset_id: str,
) -> SessionRuntimeInfo:
    """发给 renderer 的会话运行信息；settings 为会话覆盖叠加生效推理参数。"""
    return SessionRuntimeInfo(
        model=llm_config.model_name,
        provider=llm_config.provider_name,
        running=runtime.busy,
        system_preset_id=system_preset_id,
        settings=settings,
        context_window=resolve_context_tokens(llm_config.config) if llm_config.config else None,
        kind=runtime.kind,
        is_automation=runtime.is_automation,
    )
