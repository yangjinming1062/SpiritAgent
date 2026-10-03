from dataclasses import dataclass

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import MissingLlmConfigError, build_provider, resolve_provider_chain
from .providers import ChatProvider, ProviderConfig, ServiceType


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

    def chat_provider(self) -> ChatProvider:
        if self.config is None:
            raise MissingLlmConfigError("no provider configured for service 'llm'")
        return build_provider(self.config, ChatProvider)


async def resolve_user_llm_config(db: AsyncSession, user_id: int) -> UserLlmConfig:
    chain = await resolve_provider_chain(db, user_id, ServiceType.llm)
    return UserLlmConfig(chain[0] if chain else None, tuple(chain[1:]), user_id)


def client_for_config(llm_config: UserLlmConfig) -> AsyncOpenAI:
    return llm_config.chat_provider().raw_client()
