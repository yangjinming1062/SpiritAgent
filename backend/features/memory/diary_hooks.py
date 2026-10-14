"""日记删除在记忆事务中执行，由装配层注入拥有者操作。"""

from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

DiaryDelete = Callable[[AsyncSession, int, date], Awaitable[str | None]]
_delete_diary: DiaryDelete | None = None


def set_diary_delete_handler(handler: DiaryDelete) -> None:
    global _delete_diary
    _delete_diary = handler


async def delete_diary_record(db: AsyncSession, user_id: int, entry_date: date) -> str | None:
    if _delete_diary is None:
        raise RuntimeError("Diary deletion has not been registered")
    return await _delete_diary(db, user_id, entry_date)
