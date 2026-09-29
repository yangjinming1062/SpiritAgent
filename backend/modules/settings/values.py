import json
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserSetting

# user_settings.setting_value 一律 JSON 编码：桌面配置同步与服务端写入共用同一格式，读取入口统一解码为原值。


def decode_setting_value(raw: str) -> Any:
    return json.loads(raw)


async def load_user_settings(
    db: AsyncSession,
    user_id: int,
    keys: Collection[str] | None = None,
) -> dict[str, Any]:
    stmt = select(UserSetting.setting_key, UserSetting.setting_value).where(UserSetting.user_id == user_id)
    if keys is not None:
        stmt = stmt.where(UserSetting.setting_key.in_(keys))
    return {key: decode_setting_value(raw) for key, raw in (await db.execute(stmt)).all()}


async def get_user_setting(db: AsyncSession, user_id: int, key: str) -> Any:
    """未设置时返回 None。"""
    raw = (
        await db.execute(
            select(UserSetting.setting_value).where(UserSetting.user_id == user_id, UserSetting.setting_key == key),
        )
    ).scalar_one_or_none()
    return None if raw is None else decode_setting_value(raw)


async def put_user_settings(db: AsyncSession, user_id: int, values: Mapping[str, Any]) -> None:
    if not values:
        return
    stmt = insert(UserSetting).values(
        [
            {"user_id": user_id, "setting_key": key, "setting_value": json.dumps(value, ensure_ascii=False)}
            for key, value in values.items()
        ],
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "setting_key"],
            set_={"setting_value": stmt.excluded.setting_value, "updated_at": func.now()},
        ),
    )
