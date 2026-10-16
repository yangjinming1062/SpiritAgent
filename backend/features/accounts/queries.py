"""账户只读查询与事务锁。"""

from collections.abc import Collection
from dataclasses import dataclass

from infrastructure.request_errors import RequestError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User
from .snapshots import AccountIdentity


async def list_user_ids(db: AsyncSession, *, active_only: bool = False) -> list[int]:
    query = select(User.id)
    if active_only:
        query = query.where(User.is_active.is_(True))
    return list(await db.scalars(query))


async def user_is_active(db: AsyncSession, user_id: int) -> bool:
    return bool(await db.scalar(select(User.is_active).where(User.id == user_id)))


async def require_account(db: AsyncSession, user_id: int) -> AccountIdentity:
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise RequestError(status_code=404, message="用户不存在。")
    return AccountIdentity(user.id, user.username)


async def account_exists(db: AsyncSession, user_id: int) -> bool:
    return await db.scalar(select(User.id).where(User.id == user_id)) is not None


async def delete_account_record(db: AsyncSession, user_id: int) -> None:
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is not None:
        await db.delete(user)


async def lock_active_user_ids(db: AsyncSession, user_ids: Collection[int]) -> set[int]:
    return set(
        await db.scalars(
            select(User.id)
            .where(User.id.in_(sorted(user_ids)), User.is_active.is_(True))
            .order_by(User.id)
            .with_for_update(),
        ),
    )


@dataclass(frozen=True)
class NightlyAccountSnapshot:
    is_active: bool
    nightly_activity_enabled: bool


async def nightly_account(db: AsyncSession, user_id: int) -> NightlyAccountSnapshot | None:
    row = await db.get(User, user_id)
    return NightlyAccountSnapshot(row.is_active, row.nightly_activity_enabled) if row is not None else None
