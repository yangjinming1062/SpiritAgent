"""网关 RPC 的输入、结果、权限与执行策略。"""

from dataclasses import dataclass
from typing import Literal

from features.companion.idle_expression import IdleExpressionResult
from features.companion.intent_contracts import CompanionSignal
from features.companion.should_act import ShouldActResult
from features.conversation.client_contracts import ReplyBubbleView, SessionMessageView
from features.conversation.request_contracts import ChatAttachment
from features.conversation.schemas import UndoAnchor
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from runtime.conversation import LastSubmissionSnapshot, SessionRuntimeInfo, ToolSnapshot, ToolsSyncResult


class ActiveTurnView(BaseModel):
    request_id: str
    origin_kind: str
    message_ids: list[int]
    assistant_message_id: int | None
    messages: list[SessionMessageView]
    text: str
    reasoning: str
    bubbles: list[ReplyBubbleView]
    tools: list[ToolSnapshot]
    running: bool
    replace_reply_message_id: int | None


class GatewaySessionResumeResult(BaseModel):
    session_id: str
    message_count: int
    messages: list[SessionMessageView]
    info: SessionRuntimeInfo
    resumed: bool
    replayed_count: int
    current_seq: int
    stream_id: str
    active_turn: ActiveTurnView | None
    last_submission: LastSubmissionSnapshot | None
    truncated: bool
    next_cursor: str | None
    incremental: bool
    history_revision: int


class SessionUndoResult(BaseModel):
    session_id: str
    deleted_count: int
    anchor: UndoAnchor
    messages: list[SessionMessageView]


class RpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(RpcRequest):
    pass


class EmptyResult(BaseModel):
    pass


class SessionRequest(RpcRequest):
    session_id: str = Field(min_length=1)


class SessionResumeRequest(SessionRequest):
    last_seq: int | None = Field(default=None, ge=0)
    after_id: int | None = Field(default=None, ge=0)
    history_revision: int | None = Field(default=None, ge=0)
    stream_id: str | None = None


class SessionAckRequest(RpcRequest):
    seq: int = Field(ge=0)


class SessionAckResult(BaseModel):
    acked: int
    pruned: int


class RunnerToolSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ToolsSyncRequest(RpcRequest):
    skill_scope_version: Literal[1]
    tools: list[RunnerToolSchema]


class ToolResultRequest(RpcRequest):
    call_id: str = Field(min_length=1)
    result: JsonValue


class PromptBatchMessage(RpcRequest):
    text: str
    attachments: list[ChatAttachment] | None = None


class PromptSubmitRequest(SessionRequest):
    request_id: str = Field(min_length=1)
    text: str | None = None
    attachments: list[ChatAttachment] | None = None
    batch: list[PromptBatchMessage] | None = Field(default=None, min_length=1)
    edit_message_id: int | None = Field(default=None, gt=0)
    retry_message_id: int | None = Field(default=None, gt=0)
    replace_reply_message_id: int | None = Field(default=None, gt=0)
    response_preference: Literal["text", "voice"] | None = None


class PromptSubmitResult(BaseModel):
    queued: bool
    request_id: str
    status: Literal["accepted", "running", "completed", "cancelled", "interrupted", "failed"]
    error: str | None = None
    message_ids: list[int] = Field(default_factory=list)
    messages: list[SessionMessageView] = Field(default_factory=list)
    replace_reply_message_id: int | None = None
    result_message_id: int | None = None


class SessionUndoRequest(SessionRequest):
    source_message_id: int = Field(ge=0)
    confirmed: bool = False


class CompressionUsage(BaseModel):
    total_tokens: int
    context_window: int


class SessionCompressionResult(BaseModel):
    session_id: str
    compressed: bool
    reason: str | None = None
    usage: CompressionUsage
    replaced_count: int | None = None
    summary: str | None = None
    messages: list[SessionMessageView] | None = None


class CommandDispatchRequest(SessionRequest):
    command: str = Field(min_length=1)
    args: list[str | int | float] | str | None = None
    confirmed: bool = False


class CommandResult(BaseModel):
    status: Literal["ok", "error"]
    message: str
    payload: dict[str, JsonValue] | None = None
    hydrate: bool = False


class CommandDispatchResult(BaseModel):
    command: str
    result: CommandResult


class InteractionStatsRequest(RpcRequest):
    kind: Literal["chat_turn"]
    hour: int = Field(ge=0, le=23)


class InteractionStatsResult(BaseModel):
    recorded: Literal["chat_turn"]
    threshold_met: bool
    peak_hour: int | None


class IdleExpressionRequest(RpcRequest):
    trigger: Literal["periodic", "user_return"] = "periodic"
    idle_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    local_hour: int | None = Field(default=None, ge=-1, le=23)


class ShouldActRequest(RpcRequest):
    kind: Literal["periodic_provision"] = "periodic_provision"
    idle_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    local_hour: int | None = Field(default=None, ge=-1, le=23)
    focused_category: str | None = None
    fullscreen: bool = False
    screen_locked: bool = False
    seconds_since_last_action: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CompanionSignalResult(BaseModel):
    queued: bool


@dataclass(frozen=True)
class RpcContract:
    request: type[BaseModel]
    response: type[BaseModel]
    remote: bool = False
    concurrent: bool = False
    wait_for_model: bool = False


RPC_CONTRACTS: dict[str, RpcContract] = {
    "session.ack": RpcContract(SessionAckRequest, SessionAckResult, remote=True, concurrent=True),
    "session.ping": RpcContract(EmptyRequest, EmptyResult, remote=True, concurrent=True),
    "tools.sync": RpcContract(ToolsSyncRequest, ToolsSyncResult, concurrent=True),
    "tool.result": RpcContract(ToolResultRequest, EmptyResult, concurrent=True),
    "session.resume": RpcContract(SessionResumeRequest, GatewaySessionResumeResult, remote=True),
    "session.interrupt": RpcContract(SessionRequest, EmptyResult, remote=True, concurrent=True),
    "session.compress_context": RpcContract(
        SessionRequest,
        SessionCompressionResult,
        remote=True,
        concurrent=True,
        wait_for_model=True,
    ),
    "session.undo_to_message": RpcContract(SessionUndoRequest, SessionUndoResult, remote=True),
    "prompt.submit": RpcContract(PromptSubmitRequest, PromptSubmitResult, remote=True),
    "command.dispatch": RpcContract(
        CommandDispatchRequest,
        CommandDispatchResult,
        remote=True,
        concurrent=True,
        wait_for_model=True,
    ),
    "companion.record_interaction_stats": RpcContract(InteractionStatsRequest, InteractionStatsResult),
    "companion.idle_expression": RpcContract(
        IdleExpressionRequest,
        IdleExpressionResult,
        concurrent=True,
        wait_for_model=True,
    ),
    "companion.signal": RpcContract(CompanionSignal, CompanionSignalResult),
    "companion.should_act": RpcContract(ShouldActRequest, ShouldActResult, concurrent=True, wait_for_model=True),
}
