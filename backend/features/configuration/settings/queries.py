"""调度读取的账户时区快照。"""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserSetting


async def timezone_settings(db: AsyncSession, user_ids: Collection[int]) -> list[tuple[int, str]]:
    result = await db.execute(
        select(UserSetting.user_id, UserSetting.setting_value).where(
            UserSetting.user_id.in_(user_ids),
            UserSetting.setting_key == "timezone",
        ),
    )
    return list(result.tuples())
