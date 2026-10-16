from infrastructure.timezone import parse_timezone
from sqlalchemy.ext.asyncio import AsyncSession

from features.configuration.settings.values import get_user_setting


async def resolve_user_timezone(db: AsyncSession, user_id: int) -> str | None:
    """读取已校验的用户 IANA 时区；缺失或非法返回 None。"""
    zone = parse_timezone(await get_user_setting(db, user_id, "timezone"))
    return zone.key if zone is not None else None
