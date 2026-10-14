"""业务模块使用的账户事务锁，锁定范围由账户持久入口维护。"""

from sqlalchemy.ext.asyncio import AsyncSession

from .models import lock_user_row


async def lock_account(db: AsyncSession, user_id: int) -> None:
    await lock_user_row(db, user_id)
