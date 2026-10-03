from components import parse_timezone
from sqlalchemy.ext.asyncio import AsyncSession

from .values import get_user_setting, put_user_settings


async def resolve_user_timezone(db: AsyncSession, user_id: int) -> str | None:
    """读取已校验的用户 IANA 时区；缺失或非法返回 None。"""
    zone = parse_timezone(await get_user_setting(db, user_id, "timezone"))
    return zone.key if zone is not None else None


async def record_user_timezone(db: AsyncSession, user_id: int, tz: str) -> bool:
    zone = parse_timezone(tz)
    if zone is None:
        return False
    await put_user_settings(db, user_id, {"timezone": zone.key})
    return True
