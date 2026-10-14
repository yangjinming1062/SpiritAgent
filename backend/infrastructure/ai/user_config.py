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

    config: ProviderConfig | None
    fallback_configs: tuple[ProviderConfig, ...] = ()
    user_id: int | None = None

    @property
    def chain(self) -> tuple[ProviderConfig, ...]:
        return (self.config, *self.fallback_configs) if self.config is not None else ()

    @property
    def is_configured(self) -> bool:
        return self.config is not None

    @property
    def provider_name(self) -> str:
        return self.config.provider_name if self.config else ""

    @property
    def model_name(self) -> str:
        return self.config.model if self.config else ""


async def resolve_user_llm_config(
    db: AsyncSession,
    user_id: int,
    task: AiTaskKey,
    *,
    required_modalities: frozenset[ModelModality] | None = None,
) -> UserLlmConfig:
    chain = await resolve_provider_chain(
        db,
        user_id,
        ServiceType.llm,
        task=task,
        required_modalities=required_modalities,
    )
    return UserLlmConfig(chain[0] if chain else None, tuple(chain[1:]), user_id)


async def load_user_llm_config(
    user_id: int,
    task: AiTaskKey,
    *,
    required_modalities: frozenset[ModelModality] | None = None,
) -> UserLlmConfig:
    """辅助调用独立解析任务链；供应商等待期间不占数据库连接。"""
    async with SESSION_LOCAL() as db:
        return await resolve_user_llm_config(
            db,
            user_id,
            task,
            required_modalities=required_modalities,
        )
