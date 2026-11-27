from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import make_url
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.config import SETTINGS
from infrastructure.models import ModelBase


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


async def get_or_create_user_row[T: ModelBase](
    db: AsyncSession, model: type[T], user_id: int, *, lock: bool = False
) -> T:
    """取得以 user_id 为主键的状态行；并发初始化使用获胜事务的行。"""
    row = await db.get(model, user_id, with_for_update=lock, populate_existing=lock)
    if row is not None:
        return row
    created = await db.scalar(
        insert(model).values(user_id=user_id).on_conflict_do_nothing(index_elements=["user_id"]).returning(model)
    )
    if created is not None:
        return created
    return await db.get_one(model, user_id, with_for_update=lock, populate_existing=lock)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SESSION_LOCAL() as db:
        yield db


DbSession = Annotated[AsyncSession, Depends(get_db)]
