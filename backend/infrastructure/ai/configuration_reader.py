"""供应商解析读取已装配的账户配置接口，不依赖业务持久模型。"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from .config import AIConfig

type ConfigurationReader = Callable[[AsyncSession, int], Awaitable[AIConfig]]

_READER: ConfigurationReader | None = None


def set_configuration_reader(reader: ConfigurationReader) -> None:
    global _READER
    _READER = reader


async def read_user_ai_config(db: AsyncSession | None, user_id: int | None) -> AIConfig:
    if db is None or user_id is None:
        return AIConfig()
    if _READER is None:
        raise RuntimeError("Account AI configuration reader has not been registered")
    return await _READER(db, user_id)
