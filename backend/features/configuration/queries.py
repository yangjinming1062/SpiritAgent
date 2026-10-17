"""配置公开读模型。"""

from collections.abc import Collection

from infrastructure.ai.config import AIConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserModelConfig


async def user_ai_config(db: AsyncSession, user_id: int) -> AIConfig:
    raw = await db.scalar(select(UserModelConfig.ai_config).where(UserModelConfig.user_id == user_id))
    return AIConfig.model_validate(raw) if raw is not None else AIConfig()


async def user_ai_configs(db: AsyncSession, user_ids: Collection[int]) -> dict[int, AIConfig]:
    """批量读取独立配置快照；未保存配置的账户返回空配置。"""
    result = {user_id: AIConfig() for user_id in user_ids}
    if result:
        rows = await db.execute(
            select(UserModelConfig.user_id, UserModelConfig.ai_config).where(UserModelConfig.user_id.in_(result)),
        )
        result.update({user_id: AIConfig.model_validate(raw) for user_id, raw in rows})
    return result
