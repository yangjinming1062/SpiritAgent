from dataclasses import dataclass
from typing import Any

from infrastructure.ai.selection import GenerationOrigin

from features.conversation.replies import CompanionReply


@dataclass(frozen=True)
class CompressionInfo:
    summary: str
    # 检查点正文（标题行 + 摘要）：压缩当轮的上下文占位与持久化行逐字相同，后续回合读到的即当轮所见。
    checkpoint_text: str
    replaced_count: int
    prompt_tokens: int
    completion_tokens: int
    through_message_id: int
    prune_before_message_id: int | None


@dataclass(frozen=True, slots=True)
class AssistantTurnData:
    content: str
    tool_calls: list[dict[str, Any]]
    reasoning: str | None
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    reply: CompanionReply | None
    generation_origin: GenerationOrigin | None = None
