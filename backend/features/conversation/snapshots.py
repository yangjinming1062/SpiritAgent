"""回合消费的只读会话和原始消息，保留完整输入与覆盖水位。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    id: int
    user_id: int
    system_preset_id: str
    kind: str
    parent_id: int | None
    is_automation: bool
    settings_json: dict[str, Any] | None
    context_after_message_id: int
    title: str
    history_revision: int = 0


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    id: int
    conversation_id: int
    role: str
    subtype: str | None
    content: str | None
    tool_call_id: str | None
    tool_calls: list[dict[str, Any]] | None
    prompt_tokens: int
    completion_tokens: int
    turn_duration_ms: int
    content_type: str
    media_json: list[dict[str, Any]] | None
    reply_json: dict[str, Any] | None
    reasoning_content: str | None
    summary_through_message_id: int | None
    created_at: datetime
    generation_origin_json: dict[str, Any] | None = None
