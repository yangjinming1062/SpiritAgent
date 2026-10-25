"""账户内聚合的事务锁；不同业务不会竞争同一 users 行。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransaction


def transaction_owner(db: AsyncSession) -> SessionTransaction | None:
    """SAVEPOINT 回滚会释放其中取得的锁；缓存不得跨嵌套事务复用。"""
    return db.sync_session.get_nested_transaction() or db.sync_session.get_transaction()


def transaction_owns(current: SessionTransaction | None, owner: SessionTransaction | None) -> bool:
    """当前事务可复用祖先持有的锁，不能复用已经结束的子事务锁。"""
    while current is not None and current.is_active:
        if current is owner:
            return True
        current = current.parent
    return False


async def lock_domain(db: AsyncSession, user_id: int, domain: str) -> None:
    if user_id <= 0 or not domain:
        raise ValueError("领域锁需要有效账户与领域")
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"spiritagent:{domain}:{user_id}", 0))))
