import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from features.conversation.replies import CompanionReply

TrackTask = Callable[[asyncio.Task], None]
ReplyBubbleSink = Callable[[int, dict[str, Any]], Awaitable[None]]


class TerminalReplyDelivery(Protocol):
    """通话交付回执；只有完整确认的片段进入普通历史。"""

    confirmed_indices: list[int]

    async def deliver(self, reply: CompanionReply) -> None: ...


class IncrementalReplyDelivery(Protocol):
    """Optional validated-bubble sink used by streaming companion replies.

    The sink receives the raw closed JSON bubble.  It must validate and normalize it before
    producing any user-visible audio or text; terminal persistence still uses the complete reply.
    """

    async def deliver_partial(self, index: int, bubble: dict[str, Any]) -> None: ...
