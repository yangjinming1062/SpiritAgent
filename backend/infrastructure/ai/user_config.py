from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.models import ModelModality
from infrastructure.ai.providers.base import ProviderConfig, ServiceType
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.database import SESSION_LOCAL


@dataclass(frozen=True)
class UserLlmConfig:
    """已解析的用户文本能力链；首项为空时未配置。"""

    chain: tuple[ProviderConfig, ...]
    user_id: int | None = None

    @property
    def config(self) -> ProviderConfig | None:
        return self.chain[0] if self.chain else None

    @property
    def is_configured(self) -> bool:
        return bool(self.chain)

    @property
    def provider_name(self) -> str:
        return self.config.provider_name if self.config else ""

    @property
    def model_name(self) -> str:
        return self.config.model if self.config else ""


async def resolve_user_llm_config(
    db: AsyncSession,
    user_id: int | None,
    task: AiTaskKey,
    *,
    required_modalities: frozenset[ModelModality] | None = None,
) -> UserLlmConfig:
    chain = await resolve_provider_chain(
        db, user_id, ServiceType.llm, task=task, required_modalities=required_modalities
    )
    return UserLlmConfig(tuple(chain), user_id)


async def load_user_llm_config(
    user_id: int | None, task: AiTaskKey, *, required_modalities: frozenset[ModelModality] | None = None
) -> UserLlmConfig:
    """辅助调用独立解析任务链；供应商等待期间不占数据库连接。"""
    async with SESSION_LOCAL() as db:
        return await resolve_user_llm_config(db, user_id, task, required_modalities=required_modalities)
