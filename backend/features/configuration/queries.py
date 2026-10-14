"""配置公开读模型。"""

from infrastructure.ai.config import AIConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UserModelConfig


async def user_ai_config(db: AsyncSession, user_id: int) -> AIConfig:
    raw = await db.scalar(select(UserModelConfig.ai_config).where(UserModelConfig.user_id == user_id))
    return AIConfig.model_validate(raw) if raw is not None else AIConfig()
