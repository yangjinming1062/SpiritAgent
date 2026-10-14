"""全天、离线可用的独立动态决策。"""

import asyncio
import random
from time import monotonic

from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.background import TaskBag
from runtime.user_maintenance import is_user_in_maintenance, track_user_task

from features.accounts.queries import list_user_ids
from features.companion.queries import complete_user_ids
from features.posts.publication import available_types, request_publication, resume_publications
from features.posts.replies import resume_replies
from features.posts.store import PostBlockedError

logger = get_logger(__name__)
_NEXT_CHECK_AT: dict[int, float] = {}
_INFLIGHT: set[int] = set()
_BG = TaskBag("posts.autonomous")


async def scan_autonomous_posts() -> None:
    await resume_publications()
    await resume_replies()
    async with SESSION_LOCAL() as db:
        active_users = await list_user_ids(db, active_only=True)
        completed = await complete_user_ids(db, active_users)
        users = [user_id for user_id in active_users if user_id in completed]
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
        if not await available_types(user_id):
            return
        await request_publication(user_id)
    except PostBlockedError:
        logger.info("Autonomous post skipped by policy or quota", extra={"user_id": user_id})
    except Exception:
        logger.warning("Autonomous post decision failed", extra={"user_id": user_id}, exc_info=True)


async def drain_autonomous_posts() -> None:
    await _BG.drain()
