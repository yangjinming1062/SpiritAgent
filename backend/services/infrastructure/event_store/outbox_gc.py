from datetime import timedelta

from components import get_logger, session_scope, utc_now
from modules.ws import CRON_TURN_EVENT, WSEvent
from sqlalchemy import delete, or_, select

logger = get_logger(__name__)

WS_EVENT_DELIVERED_RETENTION_SECONDS = 24 * 3600
WS_EVENT_FAILED_RETENTION_SECONDS = 7 * 86400
CRON_TURN_MAX_AGE_SECONDS = 600


async def run_outbox_gc(batch_size: int = 1000) -> int:
    """分批物理清理过期的 DELIVERED / FAILED 事件与内部 cron.turn.request 行。"""
    now = utc_now()
    delivered_cutoff = now - timedelta(seconds=WS_EVENT_DELIVERED_RETENTION_SECONDS)
    failed_cutoff = now - timedelta(seconds=WS_EVENT_FAILED_RETENTION_SECONDS)
    cron_cutoff = now - timedelta(seconds=CRON_TURN_MAX_AGE_SECONDS)

    total_reaped = 0
    async with session_scope() as db:
        stmt = (
            select(WSEvent.id)
            .where(
                or_(
                    (WSEvent.status == "DELIVERED")
                    & (WSEvent.delivered_at.is_not(None))
                    & (WSEvent.delivered_at < delivered_cutoff),
                    (WSEvent.status == "DELIVERED")
                    & (WSEvent.delivered_at.is_(None))
                    & (WSEvent.created_at < delivered_cutoff),
                    (WSEvent.status == "FAILED") & (WSEvent.created_at < failed_cutoff),
                    (WSEvent.event_type == CRON_TURN_EVENT) & (WSEvent.created_at < cron_cutoff),
                ),
            )
            .limit(batch_size)
        )
        candidate_ids = (await db.execute(stmt)).scalars().all()
        if candidate_ids:
            del_stmt = delete(WSEvent).where(WSEvent.id.in_(candidate_ids))
            res = await db.execute(del_stmt)
            await db.commit()
            total_reaped = res.rowcount or len(candidate_ids)
            logger.info("WS outbox GC reaped events", extra={"reaped": total_reaped})
    return total_reaped
