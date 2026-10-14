"""Outbox 的引用与历史失效操作。"""

import json
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WSEvent


async def live_event_payloads(db: AsyncSession, user_id: int) -> tuple[str, ...]:
    return tuple(
        await db.scalars(
            select(WSEvent.payload).where(WSEvent.user_id == user_id, WSEvent.status.in_(("PENDING", "PROCESSING"))),
        ),
    )


async def settled_event_payloads(db: AsyncSession, user_id: int) -> tuple[tuple[int, str], ...]:
    return tuple(
        (row.id, row.payload)
        for row in await db.scalars(
            select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.status.not_in(("PENDING", "PROCESSING"))),
        )
    )


async def remove_events(db: AsyncSession, user_id: int, ids: Collection[int]) -> None:
    for row in await db.scalars(select(WSEvent).where(WSEvent.user_id == user_id, WSEvent.id.in_(ids))):
        await db.delete(row)


async def invalidate_history_events(
    db: AsyncSession,
    user_id: int,
    message_ids: Collection[int],
    job_ids: Collection[str],
    session_ids: Collection[str],
) -> tuple[str, ...]:
    released: list[str] = []
    for event in await db.scalars(select(WSEvent).where(WSEvent.user_id == user_id)):
        try:
            payload = json.loads(event.payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        belongs = (
            payload.get("message_id") in message_ids
            or payload.get("status_message_id") in message_ids
            or event.event_type.startswith("video_gen.")
            and str(payload.get("task_id")) in job_ids
            or str(payload.get("session_id")) in session_ids
            and event.event_type
            in {"message.voice", "message.media", "companion.message", "video_gen.completed", "video_gen.failed"}
        )
        if belongs:
            released.append(event.payload)
            await db.delete(event)
    return tuple(released)
