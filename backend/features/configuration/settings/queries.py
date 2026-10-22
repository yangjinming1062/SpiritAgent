"""调度读取的账户时区快照。"""

from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserSetting
from .values import decode_setting_value


async def timezone_settings(db: AsyncSession, user_ids: Collection[int]) -> list[tuple[int, Any]]:
    result = await db.execute(
        select(UserSetting.user_id, UserSetting.setting_value).where(
            UserSetting.user_id.in_(user_ids),
            UserSetting.setting_key == "timezone",
        ),
    )
    return [(user_id, decode_setting_value(raw)) for user_id, raw in result.tuples()]
