"""HTTP 业务请求与序列化结果；路径身份由路由提供。"""

from typing import Literal

from infrastructure.constants import POSTGRES_INT4_MAX
from pydantic import BaseModel, ConfigDict, Field, model_validator
from runtime.conversation import LastSubmissionSnapshot, SessionRuntimeInfo, ToolSnapshot

from features.conversation.client_contracts import ReplyBubbleView, SessionMessageView
from features.conversation.request_contracts import ChatAttachment
from features.conversation.schemas import UndoAnchor


class BusinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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


class SessionSnapshotResponse(BaseModel):
    session_id: str
    message_count: int
    messages: list[SessionMessageView]
    info: SessionRuntimeInfo
    current_seq: int | None = None
    stream_id: str | None = None
    active_turn: ActiveTurnView | None
    last_submission: LastSubmissionSnapshot | None
    truncated: bool
    next_cursor: str | None
    history_revision: int


class SessionUndoResult(BaseModel):
    session_id: str
    deleted_count: int
    anchor: UndoAnchor
    messages: list[SessionMessageView]


class PromptBatchMessage(BusinessRequest):
    text: str = Field(pattern=r"^[^\x00]*$")
    attachments: list[ChatAttachment] | None = None


class PromptSubmitRequest(BusinessRequest):
    request_id: str = Field(min_length=1)
    text: str | None = Field(default=None, pattern=r"^[^\x00]*$")
    attachments: list[ChatAttachment] | None = None
    batch: list[PromptBatchMessage] | None = Field(default=None, min_length=1)
    edit_message_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)
    retry_message_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)
    replace_reply_message_id: int | None = Field(default=None, gt=0, le=POSTGRES_INT4_MAX)
    response_preference: Literal["text", "voice"] | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "PromptSubmitRequest":
        supplied = self.model_fields_set
        exclusive = [self.retry_message_id, self.replace_reply_message_id]
        if sum(value is not None for value in exclusive) > 1:
            raise ValueError("retry and replacement cannot be combined")
        if any(value is not None for value in exclusive):
            if supplied & {"text", "batch", "attachments", "edit_message_id"}:
                raise ValueError("retry and replacement reuse the original input")
            if self.retry_message_id is not None and "replace_reply_message_id" in supplied:
                raise ValueError("reply retries reuse the original message and attachments")
            if self.replace_reply_message_id is not None and "retry_message_id" in supplied:
                raise ValueError("answer replacement reuses the original turn")
        elif self.edit_message_id is not None:
            if self.text is None or supplied & {"batch", "attachments"}:
                raise ValueError("message edits require text and retain original attachments")
        elif (self.text is None) == (self.batch is None):
            raise ValueError("provide exactly one of text or batch")
        elif self.batch is not None and "attachments" in supplied:
            raise ValueError("batch attachments must belong to individual messages")
        return self


class PromptSubmitResult(BaseModel):
    queued: bool
    request_id: str
    status: Literal["accepted", "running", "completed", "cancelled", "interrupted", "failed"]
    error: str | None = None
    message_ids: list[int] = Field(default_factory=list)
    messages: list[SessionMessageView] = Field(default_factory=list)
    replace_reply_message_id: int | None = None
    result_message_id: int | None = None


class SessionUndoRequest(BusinessRequest):
    source_message_id: int = Field(ge=0, le=POSTGRES_INT4_MAX)
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


class CommandDispatchRequest(BusinessRequest):
    command: str = Field(min_length=1)
    args: list[str | int | float] | str | None = None
    confirmed: bool = False


class SessionClearResult(BaseModel):
    session_id: str
    cleared_count: int
    messages: list[SessionMessageView]


class RememberCommandResult(BaseModel):
    memory_id: int
    content: str


class CommandResult(BaseModel):
    status: Literal["ok", "error"]
    message: str
    payload: SessionClearResult | SessionCompressionResult | RememberCommandResult | None = None
    hydrate: bool = False


class CommandDispatchResult(BaseModel):
    command: str
    result: CommandResult


class TurnCancelResult(BaseModel):
    request_id: str
    cancelled: bool
