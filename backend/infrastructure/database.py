from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.config import SETTINGS


def database_url(drivername: str) -> str:
    """按驱动改写配置里的数据库 URL（asyncpg 引擎、psycopg 迁移、纯 postgresql LISTEN 专线）。"""
    return make_url(SETTINGS.database_url).set(drivername=drivername).render_as_string(hide_password=False)


ENGINE: AsyncEngine = create_async_engine(
    database_url("postgresql+asyncpg"),
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    hide_parameters=True,
)

SESSION_LOCAL = async_sessionmaker(ENGINE, autoflush=False, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SESSION_LOCAL() as db:
        yield db


DbSession = Annotated[AsyncSession, Depends(get_db)]
