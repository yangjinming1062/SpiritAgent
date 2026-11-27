"""配置公开读模型。"""

from collections.abc import Collection

from infrastructure.ai.config import AIConfig
from infrastructure.models import id_in
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserModelConfig


async def user_ai_config(db: AsyncSession, user_id: int) -> AIConfig:
    raw = await db.scalar(select(UserModelConfig.ai_config).where(UserModelConfig.user_id == user_id))
    return AIConfig.model_validate(raw) if raw is not None else AIConfig()


async def user_ai_configs(db: AsyncSession, user_ids: Collection[int]) -> dict[int, AIConfig]:
    """按账户读取已保存的模型配置。"""
    if not user_ids:
        return {}
    rows = await db.execute(
        select(UserModelConfig.user_id, UserModelConfig.ai_config).where(id_in(UserModelConfig.user_id, user_ids))
    )
    return {user_id: AIConfig.model_validate(raw) for user_id, raw in rows}
