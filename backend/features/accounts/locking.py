"""账户事务锁；身份管理和需共同提交的账户操作共用。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User


async def lock_account(db: AsyncSession, user_id: int) -> None:
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
