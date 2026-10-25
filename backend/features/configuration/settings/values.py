from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.models import UserSetting


async def load_user_settings(
    db: AsyncSession,
    user_id: int,
    keys: Collection[str] | None = None,
) -> dict[str, Any]:
    stmt = select(UserSetting.setting_key, UserSetting.setting_value).where(UserSetting.user_id == user_id)
    if keys is not None:
        stmt = stmt.where(UserSetting.setting_key.in_(keys))
    return dict((await db.execute(stmt)).all())


async def get_user_setting(db: AsyncSession, user_id: int, key: str) -> Any:
    """未设置时返回 None。"""
    return await db.scalar(
        select(UserSetting.setting_value).where(UserSetting.user_id == user_id, UserSetting.setting_key == key),
    )


async def get_user_setting_bool(db: AsyncSession, user_id: int, key: str, default: bool) -> bool:
    """未设置取 default；仅字面 True 为真，其余存储值按假。"""
    value = await get_user_setting(db, user_id, key)
    return default if value is None else value is True


async def lock_user_setting(db: AsyncSession, user_id: int, key: str, default: Any) -> Any:
    """建立并锁定设置行，供需要比较后提交的跨业务操作使用。"""
    await db.execute(
        insert(UserSetting)
        .values(user_id=user_id, setting_key=key, setting_value=default)
        .on_conflict_do_nothing(index_elements=["user_id", "setting_key"]),
    )
    return await db.scalar(
        select(UserSetting.setting_value)
        .where(UserSetting.user_id == user_id, UserSetting.setting_key == key)
        .with_for_update(),
    )


async def put_user_settings(db: AsyncSession, user_id: int, values: Mapping[str, Any]) -> None:
    if not values:
        return
    stmt = insert(UserSetting).values(
        [{"user_id": user_id, "setting_key": key, "setting_value": value} for key, value in values.items()],
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "setting_key"],
            set_={"setting_value": stmt.excluded.setting_value, "updated_at": func.now()},
        ),
    )
