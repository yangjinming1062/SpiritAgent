"""admin resource operations; transaction and failure semantics belong to the owning capability."""

from typing import Any

from infrastructure.ai.config import CAPABILITY_SERVICES, AIConfig, ModelDiscoveryRequest
from infrastructure.ai.models import ModelDiscoveryResponse
from infrastructure.ai.providers.registry import provider_configuration_defaults, providers_supporting
from infrastructure.ai.tasks import AI_TASKS
from infrastructure.config import SETTINGS
from infrastructure.queries import get_or_404
from infrastructure.request_errors import RequestError
from runtime.responses import MessageResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.queries import require_account
from features.accounts.schemas import UserModelConfigListItem, UserModelConfigListResponse, UserModelConfigRequest
from features.configuration.ai_config import prepare_ai_config, public_ai_config
from features.configuration.lock import configuration_lock
from features.configuration.model_discovery import discover_configured_models
from features.configuration.models import UserModelConfig
from features.configuration.system_settings import get_system_settings_for_admin, save_system_settings


async def discover_ai_models(payload: ModelDiscoveryRequest, db: AsyncSession) -> ModelDiscoveryResponse:
    try:
        return await discover_configured_models(db, payload)
    except ValueError as exc:
        raise RequestError(status_code=422, message=str(exc)) from None


def _config_list_item(r: UserModelConfig) -> UserModelConfigListItem:
    return UserModelConfigListItem(user_id=r.user_id, ai_config=public_ai_config(AIConfig.model_validate(r.ai_config)))


async def list_model_configs(db: AsyncSession) -> UserModelConfigListResponse:
    support = {service: providers_supporting(service) for service in CAPABILITY_SERVICES}
    return UserModelConfigListResponse(
        items=[_config_list_item(r) for r in (await db.execute(select(UserModelConfig))).scalars().all()],
        ai_provider_support=support,
        ai_provider_defaults=provider_configuration_defaults(),
        system_ai_config=public_ai_config(SETTINGS.ai_config),
        ai_tasks=list(AI_TASKS),
    )


async def update_system_settings(payload: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """更新系统动态配置，实时持久化到数据库并热重载生效。"""
    try:
        await save_system_settings(db, payload)
    except ValueError as exc:
        raise RequestError(status_code=422, message=str(exc)) from exc
    return get_system_settings_for_admin()


async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, db: AsyncSession) -> MessageResponse:
    async with configuration_lock:
        await require_account(db, user_id)
        config = (
            await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))
        ).scalar_one_or_none()
        previous = AIConfig.model_validate(config.ai_config) if config else None
        try:
            ai_config = prepare_ai_config(payload.ai_config, previous, SETTINGS.ai_config)
        except ValueError as exc:
            raise RequestError(status_code=422, message=str(exc)) from exc
        if config:
            config.ai_config = ai_config.model_dump(mode="json")
        else:
            db.add(UserModelConfig(user_id=user_id, ai_config=ai_config.model_dump(mode="json")))
        await db.commit()
    return MessageResponse(message="模型配置已更新。")


async def delete_model_config(user_id: int, db: AsyncSession) -> MessageResponse:
    async with configuration_lock:
        await db.delete(await get_or_404(db, UserModelConfig, user_id=user_id, message="模型配置不存在。"))
        await db.commit()
    return MessageResponse(message="模型配置已删除。")
