"""全天、离线可用的独立动态决策。"""

import asyncio
import random
from time import monotonic
from uuid import uuid4

from components import SESSION_LOCAL, TaskBag, get_logger, is_user_in_maintenance, track_user_task
from modules.auth import User
from modules.companion import Persona
from sqlalchemy import select

from .publication import available_types, request_publication, resume_publications
from .replies import resume_replies

logger = get_logger(__name__)
_NEXT_CHECK_AT: dict[int, float] = {}
_INFLIGHT: set[int] = set()
_BG = TaskBag("posts.autonomous")


async def scan_autonomous_posts() -> None:
    await resume_publications()
    await resume_replies()
    async with SESSION_LOCAL() as db:
        users = list(
            (
                await db.scalars(
                    select(User.id)
                    .join(Persona, Persona.user_id == User.id)
                    .where(
                        User.is_active.is_(True),
                        Persona.is_complete.is_(True),
                    ),
                )
            ).all(),
        )
    now = monotonic()
    for user_id in users:
        due = _NEXT_CHECK_AT.setdefault(user_id, now + random.uniform(45 * 60, 90 * 60))
        if now < due or user_id in _INFLIGHT or is_user_in_maintenance(user_id):
            continue
        _NEXT_CHECK_AT[user_id] = now + random.uniform(45 * 60, 90 * 60)
        _INFLIGHT.add(user_id)
        task = asyncio.create_task(_decide(user_id))
        task.add_done_callback(lambda _, uid=user_id: _INFLIGHT.discard(uid))
        _BG.add(task)
        track_user_task(user_id, task)
    for stale in set(_NEXT_CHECK_AT) - set(users):
        _NEXT_CHECK_AT.pop(stale, None)


async def _decide(user_id: int) -> None:
    try:
        if not await available_types(user_id, autonomous=True):
            return
        await request_publication(
            user_id,
            key=f"autonomous:{uuid4()}",
            trigger="autonomous",
            intent="结合此刻的生活和心情，决定是否有值得分享的新动态。",
        )
    except Exception:
        logger.warning("Autonomous post decision failed", extra={"user_id": user_id}, exc_info=True)


async def drain_autonomous_posts() -> None:
    await _BG.drain()
