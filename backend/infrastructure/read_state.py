"""账户作用域的已读状态表操作；锁与提交通知由业务拥有者提供。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ModelBase

AccountLock = Callable[[AsyncSession, int], Awaitable[None]]


@dataclass(frozen=True)
class ReadStateTable:
    _model: type[ModelBase]


async def unread_ids(db: AsyncSession, user_id: int, descriptor: ReadStateTable) -> list[str]:
    columns = descriptor._model
    return list(await db.scalars(select(columns.id).where(columns.user_id == user_id, columns.is_read.is_(False))))


async def has_unread(db: AsyncSession, user_id: int, descriptor: ReadStateTable) -> bool:
    columns = descriptor._model
    return bool(
        await db.scalar(
            select(select(columns.id).where(columns.user_id == user_id, columns.is_read.is_(False)).exists()),
        ),
    )


async def mark_read(
    db: AsyncSession,
    user_id: int,
    descriptor: ReadStateTable,
    ids: Sequence[str],
    *,
    lock_account: AccountLock,
    notify: Callable[[], None],
) -> bool:
    """锁定账户后条件更新并同事务通知；重复标记不重复发事件。"""
    await lock_account(db, user_id)
    model = descriptor._model
    result = await db.execute(
        update(model).where(model.user_id == user_id, model.id.in_(ids), model.is_read.is_(False)).values(is_read=True),
    )
    if result.rowcount:
        notify()
    remaining = await has_unread(db, user_id, descriptor)
    await db.commit()
    return remaining
