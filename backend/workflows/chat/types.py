import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from features.companion.intent_contracts import DeferredReply
from features.conversation.replies import CompanionReply
from infrastructure.ai.selection import GenerationOrigin

TrackTask = Callable[[asyncio.Task], None]
ReplyBubbleSink = Callable[[int, dict[str, Any]], Awaitable[asyncio.Future[None]]]


@dataclass
class LLMTurnResult:
    """单次模型调用的可交付结果。工具标识由编排层分配。"""

    turn_content: str
    tool_calls_list: list[dict]
    final_prompt_tokens: int
    final_completion_tokens: int
    final_usage_payload: dict | None
    turn_duration_ms: int
    reasoning: str | None = None
    reply: CompanionReply | None = None
    generation_origin: GenerationOrigin | None = None
    decision: Literal["silent", "deferred"] | None = None
    followup: DeferredReply | None = None


class LifeContactEvidence(TypedDict):
    selected_experience: dict[str, str]
    recent_dialogue: str


class TerminalReplyDelivery(Protocol):
    """通话交付回执；已开始播放或完整确认的片段进入通话记录。"""

    async def deliver(self, reply: CompanionReply) -> None: ...

    async def enqueue_partial(self, index: int, bubble: dict[str, Any]) -> asyncio.Future[None]: ...

    async def stop_partial_delivery(self) -> None: ...

    def delivered_indices_for(self, request_id: str | None) -> list[int]: ...

    def mark_persisted(self, indices: list[int]) -> None: ...

    def partial_reply(self, request_id: str) -> tuple[CompanionReply, str] | None: ...

    def clear_partial_reply(self, request_id: str) -> None: ...
