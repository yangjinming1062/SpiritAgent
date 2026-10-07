from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import resolve_provider_chain
from .providers import ProviderConfig, ServiceType


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


async def resolve_user_llm_config(db: AsyncSession, user_id: int) -> UserLlmConfig:
    chain = await resolve_provider_chain(db, user_id, ServiceType.llm)
    return UserLlmConfig(chain[0] if chain else None, tuple(chain[1:]), user_id)
