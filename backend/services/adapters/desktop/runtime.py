import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from components import safe_json_loads
from pydantic import BaseModel, ConfigDict, Field

from services.infrastructure.llm import ServiceType, resolve_context_tokens


class SessionSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    temperature: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    context_compression_threshold: float | None = Field(default=None, ge=0.3, le=1, allow_inf_nan=False)
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None


class SessionRuntimeInfo(BaseModel):
    cwd: str | None
    branch: str | None
    model: str | None
    provider: str
    running: bool
    settings: dict[str, Any] = Field(default_factory=dict)
    context_window: int | None = None
    # 客户端 IM 守卫与语音入口的权威判定源，避免依赖尚未加载的会话列表。
    kind: str = "standard"


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


class ToolsSyncResult(BaseModel):
    count: int


@dataclass
class RuntimeSession:
    conversation_id: int
    chat_task: asyncio.Task | None = None
    cwd: str | None = None
    # 会话级覆盖（reasoning/fast/language）：mount 时镜像 Conversation.settings_json；set_setting 修改时也会写回 DB，重连后会读回相同值。
    settings: dict[str, Any] = field(default_factory=dict)
    # 会话种类镜像（special/standard/im）：prompt_submit 据此拒绝 im 渠道会话（由通道桥独占写入）。
    kind: str = "standard"

    @property
    def session_id(self) -> str:
        """renderer 侧 id（Conversation.id 的一次性字符串化）。"""
        return str(self.conversation_id)


def new_runtime_session(
    conversation_id: int,
    cwd: str | None,
    settings_json: str | None = None,
    kind: str = "standard",
) -> RuntimeSession:
    """为已存在的 DB 会话创建 runtime wrapper；settings_json 是 Conversation.settings_json 的原始 JSON 字符串，解码到 settings 让 per-turn 逻辑不必每次回查 DB。"""
    decoded = safe_json_loads(settings_json)
    settings = decoded if isinstance(decoded, dict) else {}
    return RuntimeSession(conversation_id=conversation_id, cwd=cwd, settings=settings, kind=kind)


def runtime_info_snapshot(llm_config: dict[str, Any], runtime: RuntimeSession) -> dict[str, Any]:
    """发给 renderer 的 SessionRuntimeInfo 负载。renderer 容忍缺失字段，未读的 settings 键不在契约内。"""
    provider = llm_config.get("provider") or llm_config.get("provider_name") or "openai"
    context_window = resolve_context_tokens(provider, ServiceType.llm)

    return {
        "cwd": runtime.cwd,
        "branch": None,
        "model": llm_config.get("model_name"),
        "provider": provider,
        "running": bool(runtime.chat_task and not runtime.chat_task.done()),
        "settings": dict(runtime.settings),
        "context_window": context_window,
        "kind": runtime.kind,
    }
