from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import resolve_provider_chain


class UserLlmConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    provider_name: str = ""


async def resolve_user_llm_config(db: AsyncSession, user_id: int) -> UserLlmConfig:
    chain = await resolve_provider_chain(db, user_id, "llm")
    head = chain[0] if chain else None
    return UserLlmConfig(
        api_key=head.api_key if head else "",
        base_url=head.base_url if head else "",
        model_name=head.model if head else "",
        provider_name=head.provider_name if head else "",
    )
