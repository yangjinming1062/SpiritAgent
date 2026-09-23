from modules.auth import UserModelConfig
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import resolve_provider_chain


class UserLlmConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    provider_name: str = ""


async def resolve_user_llm_config(db: AsyncSession, user_id: int) -> UserLlmConfig:
    # 所有凭据都来自 chat 路径同一链头，下游调用方（scheduler、title 生成）看到一致的供应商。
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    chain = await resolve_provider_chain(db, user_id, "llm", user_cfg=config)
    head = chain[0] if chain else None
    return UserLlmConfig(
        api_key=head.api_key if head else "",
        base_url=head.base_url if head else "",
        model_name=head.model if head else "",
        provider_name=head.provider_name if head else "",
    )
