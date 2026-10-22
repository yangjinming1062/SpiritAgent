from urllib.parse import urlsplit

from infrastructure.ai.config import ModelDiscoveryRequest
from infrastructure.ai.model_discovery import discover_models
from infrastructure.ai.models import ModelDiscoveryResponse
from infrastructure.ai.providers.base import ServiceType
from infrastructure.ai.providers.registry import configured_base_url, providers_supporting
from infrastructure.config import SETTINGS
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import account_exists
from features.configuration.ai_config import prepare_provider_card
from features.configuration.queries import user_ai_config


async def discover_configured_models(db: AsyncSession, request: ModelDiscoveryRequest) -> ModelDiscoveryResponse:
    if request.provider.provider_type not in providers_supporting(request.service):
        raise ValueError("此供应商适配器不支持所选能力")
    if request.user_id is None:
        previous = SETTINGS.ai_config.find_provider(request.provider.id)
        provider = prepare_provider_card(request.provider, previous)
    else:
        if not await account_exists(db, request.user_id):
            raise ValueError("用户不存在")
        system_provider = SETTINGS.ai_config.find_provider(request.provider.id)
        if system_provider is not None:
            # 用户模型引用系统实例时，端点与密钥始终归系统信息库。
            provider = system_provider.model_copy(deep=True)
        else:
            user = await user_ai_config(db, request.user_id)
            previous = user.find_provider(request.provider.id)
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
    await db.commit()
    return await discover_models(
        provider,
        ServiceType(request.service),
        model_name=request.model_name,
        catalog_id=request.catalog_id,
        refresh=request.refresh,
    )
