import asyncio
from collections.abc import Callable
from typing import Protocol

from features.conversation.replies import CompanionReply

TrackTask = Callable[[asyncio.Task], None]


class TerminalReplyDelivery(Protocol):
    """通话交付回执；只有完整确认的片段进入普通历史。"""

    confirmed_indices: list[int]

    async def deliver(self, reply: CompanionReply) -> None: ...
