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
    settings: dict[str, Any] = Field(default_factory=dict)
    context_window: int | None = None
    # 客户端 IM 守卫与语音入口的权威判定源，避免依赖尚未加载的会话列表。
    kind: str = "standard"
    is_automation: bool = False


class SessionCreateResult(BaseModel):
    session_id: str
    info: SessionRuntimeInfo


class SessionResumeResult(BaseModel):
    session_id: str
    message_count: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    info: SessionRuntimeInfo
    resumed: bool = False
    replayed_count: int = 0
    current_seq: int = 0
    truncated: bool = False
    next_cursor: str | None = None
    # after_id 命中且锚点仍存在时为 True：messages 仅为增量，客户端按 id 合并本地缓存。
    incremental: bool = False


class ToolsSyncResult(BaseModel):
    count: int


@dataclass
class RuntimeSession:
    conversation_id: int
    chat_task: asyncio.Task | None = None
    # 会话级覆盖（temperature / reasoning_effort / context_compression_threshold）：mount 时镜像 Conversation.settings_json；set_settings 修改时也会写回 DB，重连后会读回相同值。
    settings: dict[str, Any] = field(default_factory=dict)
    # 会话种类镜像（special/standard/im）：prompt_submit 据此拒绝 im 渠道会话（由通道桥独占写入）。
    kind: str = "standard"
    is_automation: bool = False

    @property
    def session_id(self) -> str:
        """renderer 侧 id（Conversation.id 的一次性字符串化）。"""
        return str(self.conversation_id)

    @property
    def busy(self) -> bool:
        return self.chat_task is not None and not self.chat_task.done()


def decode_session_settings(settings_json: str | None) -> dict[str, Any]:
    decoded = safe_json_loads(settings_json or "")
    return decoded if isinstance(decoded, dict) else {}


def new_runtime_session(conv: Conversation) -> RuntimeSession:
    """为已存在的 DB 会话创建 runtime；settings 解码自 Conversation.settings_json，让回合逻辑不必每次回查 DB。"""
    return RuntimeSession(
        conversation_id=conv.id,
        settings=decode_session_settings(conv.settings_json),
        kind=conv.kind,
        is_automation=conv.is_automation,
    )


def build_runtime_info(
    llm_config: UserLlmConfig,
    runtime: RuntimeSession,
    settings: dict[str, Any],
) -> SessionRuntimeInfo:
    """发给 renderer 的会话运行信息；settings 为会话覆盖叠加生效推理参数。"""
    provider = llm_config.provider_name or "openai"
    return SessionRuntimeInfo(
        model=llm_config.model_name,
        provider=provider,
        running=runtime.busy,
        settings=settings,
        context_window=resolve_context_tokens(provider),
        kind=runtime.kind,
        is_automation=runtime.is_automation,
    )
