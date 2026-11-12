import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from features.conversation.replies import CompanionReply

TrackTask = Callable[[asyncio.Task], None]
ReplyBubbleSink = Callable[[int, dict[str, Any]], Awaitable[Any]]


class TerminalReplyDelivery(Protocol):
    """通话交付回执；只有完整确认的片段进入普通历史。"""

    confirmed_indices: list[int]

    async def deliver(self, reply: CompanionReply) -> None: ...


class IncrementalReplyDelivery(Protocol):
    """Optional normalized-bubble sink used by streaming companion replies.

    The sink receives a closed dialogue bubble after the normal sentence split, speech binding,
    and reply validation. Terminal persistence still uses the complete reply.
    """

    async def deliver_partial(self, index: int, bubble: dict[str, Any]) -> None: ...

    def partial_reply(self) -> tuple[CompanionReply, str] | None: ...
