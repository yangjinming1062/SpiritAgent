from datetime import timedelta

from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from sqlalchemy import delete, or_, select

from runtime.events.contracts import COMPANION_TURN_EVENTS
from runtime.events.models import WSEvent

logger = get_logger(__name__)

WS_EVENT_DELIVERED_RETENTION_SECONDS = 24 * 3600
WS_EVENT_FAILED_RETENTION_SECONDS = 7 * 86400
COMPANION_TURN_MAX_AGE_SECONDS = 600
# 单次调用的清理上限；剩余行由下一次调度继续。
OUTBOX_GC_BATCH_SIZE = 1000


async def run_outbox_gc() -> None:
    """分批物理清理过期的已交付、失败及内部陪伴回合事件。"""
    now = utc_now()
    delivered_cutoff = now - timedelta(seconds=WS_EVENT_DELIVERED_RETENTION_SECONDS)
    failed_cutoff = now - timedelta(seconds=WS_EVENT_FAILED_RETENTION_SECONDS)
    companion_cutoff = now - timedelta(seconds=COMPANION_TURN_MAX_AGE_SECONDS)

    async with SESSION_LOCAL() as db:
        stmt = (
            select(WSEvent.id)
            .where(
                or_(
                    # 送达标记总是同时写 delivered_at。
                    (WSEvent.status == "DELIVERED") & (WSEvent.delivered_at < delivered_cutoff),
                    (WSEvent.status == "FAILED") & (WSEvent.created_at < failed_cutoff),
                    WSEvent.event_type.in_(COMPANION_TURN_EVENTS) & (WSEvent.created_at < companion_cutoff),
                )
            )
            .limit(OUTBOX_GC_BATCH_SIZE)
        )
        candidate_ids = (await db.execute(stmt)).scalars().all()
        if not candidate_ids:
            return
        await db.execute(delete(WSEvent).where(WSEvent.id.in_(candidate_ids)))
        await db.commit()
    logger.info("WS outbox GC reaped events", extra={"reaped": len(candidate_ids)})
