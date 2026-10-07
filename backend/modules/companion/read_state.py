"""日记与动态共用的已读状态事务契约：锁行→条件更新→同事务 outbox→返回剩余未读。"""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.auth import lock_user_row
from modules.ws import emit_ws_event

from .journal import CompanionDiaryEntry
from .posts import CompanionPost


async def unread_ids[RowT: CompanionDiaryEntry | CompanionPost](
    db: AsyncSession,
    user_id: int,
    model: type[RowT],
) -> list[str]:
    return list(
        (await db.scalars(select(model.id).where(model.user_id == user_id, model.is_read.is_(False)))).all(),
    )


async def has_unread[RowT: CompanionDiaryEntry | CompanionPost](
    db: AsyncSession,
    user_id: int,
    model: type[RowT],
) -> bool:
    return bool(
        await db.scalar(
            select(select(model.id).where(model.user_id == user_id, model.is_read.is_(False)).exists()),
        ),
    )


async def mark_read[RowT: CompanionDiaryEntry | CompanionPost](
    db: AsyncSession,
    user_id: int,
    model: type[RowT],
    ids: Sequence[str],
    event_type: str,
) -> bool:
    """提交事务并返回剩余未读；is_read 守卫保证重复标记不重复发事件。"""
    await lock_user_row(db, user_id)
    result = await db.execute(
        update(model)
        .where(
            model.user_id == user_id,
            model.id.in_(ids),
            model.is_read.is_(False),
        )
        .values(is_read=True),
    )
    if result.rowcount:
        emit_ws_event(db, user_id=user_id, event_type=event_type, payload={})
    remaining = await has_unread(db, user_id, model)
    await db.commit()
    return remaining
