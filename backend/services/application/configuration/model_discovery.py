from urllib.parse import urlsplit

from components import SETTINGS, AIConfig, ModelDiscoveryRequest, ModelDiscoveryResponse
from modules.auth import User, UserModelConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.configuration import prepare_provider_card
from services.infrastructure.llm import ServiceType, configured_base_url, discover_models, providers_supporting


async def discover_configured_models(db: AsyncSession, request: ModelDiscoveryRequest) -> ModelDiscoveryResponse:
    if request.provider.provider_type not in providers_supporting(request.service):
        raise ValueError("此供应商适配器不支持所选能力")
    if request.user_id is None:
        previous = SETTINGS.ai_config.find_provider(request.provider.id)
    else:
        if await db.get(User, request.user_id) is None:
            raise ValueError("用户不存在")
        row = await db.scalar(select(UserModelConfig).where(UserModelConfig.user_id == request.user_id))
        user = AIConfig.model_validate(row.ai_config) if row is not None else AIConfig()
        previous = user.find_provider(request.provider.id)
        system_provider = SETTINGS.ai_config.find_provider(request.provider.id)
        if system_provider is not None:
            # 用户模型引用系统实例时，端点与密钥始终归系统信息库。
            provider = system_provider.model_copy(deep=True)
        else:
            provider = prepare_provider_card(request.provider, previous)
    if request.user_id is None:
        provider = prepare_provider_card(request.provider, previous)
    base = configured_base_url(provider.provider_type, provider.base_url, ServiceType(request.service))
    url = urlsplit(base)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("请填写有效的 HTTP(S) Base URL，不包含查询参数或地址内凭据")
    return await discover_models(
        provider,
        ServiceType(request.service),
        model_name=request.model_name,
        catalog_id=request.catalog_id,
        refresh=request.refresh,
    )
