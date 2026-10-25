"""Outbox 的引用与历史失效操作。"""

from collections.abc import Collection
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WSEvent


async def live_event_payloads(db: AsyncSession, user_id: int) -> tuple[dict[str, Any], ...]:
    return tuple(
        await db.scalars(
            select(WSEvent.payload).where(WSEvent.user_id == user_id, WSEvent.status.in_(("PENDING", "PROCESSING"))),
        ),
    )


async def settled_event_payloads(db: AsyncSession, user_id: int) -> tuple[tuple[int, dict[str, Any]], ...]:
    return tuple(
        (row.id, row.payload)
        for row in await db.scalars(
            select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.status.not_in(("PENDING", "PROCESSING"))),
        )
    )


async def remove_events(db: AsyncSession, user_id: int, ids: Collection[int]) -> None:
    if not ids:
        return
    await db.execute(delete(WSEvent).where(WSEvent.user_id == user_id, WSEvent.id.in_(ids)))


async def invalidate_history_events(
    db: AsyncSession,
    user_id: int,
    message_ids: Collection[int],
    job_ids: Collection[str],
    session_ids: Collection[str],
) -> tuple[dict[str, Any], ...]:
    message_keys = [str(value) for value in message_ids]
    job_keys = [str(value) for value in job_ids]
    session_keys = [str(value) for value in session_ids]
    if not message_keys and not job_keys and not session_keys:
        return ()
    belongs = or_(
        WSEvent.payload["message_id"].astext.in_(message_keys),
        WSEvent.payload["status_message_id"].astext.in_(message_keys),
        and_(WSEvent.event_type.startswith("video_gen."), WSEvent.payload["task_id"].astext.in_(job_keys)),
        and_(
            WSEvent.payload["session_id"].astext.in_(session_keys),
            WSEvent.event_type.in_(
                ("message.voice", "message.media", "companion.message", "video_gen.completed", "video_gen.failed")
            ),
        ),
    )
    return tuple(
        await db.scalars(delete(WSEvent).where(WSEvent.user_id == user_id, belongs).returning(WSEvent.payload))
    )
