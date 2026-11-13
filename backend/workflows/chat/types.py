import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from features.conversation.replies import CompanionReply

TrackTask = Callable[[asyncio.Task], None]
ReplyBubbleSink = Callable[[int, dict[str, Any]], Awaitable[asyncio.Future[None]]]


class TerminalReplyDelivery(Protocol):
    """通话交付回执；只有完整确认的片段进入普通历史。"""

    async def deliver(self, reply: CompanionReply) -> None: ...

    async def enqueue_partial(self, index: int, bubble: dict[str, Any]) -> asyncio.Future[None]: ...

    async def stop_partial_delivery(self) -> None: ...

    def confirmed_indices_for(self, request_id: str | None) -> list[int]: ...

    def mark_persisted(self, indices: list[int]) -> None: ...

    def partial_reply(self, request_id: str) -> tuple[CompanionReply, str] | None: ...

    def clear_partial_reply(self, request_id: str) -> None: ...
