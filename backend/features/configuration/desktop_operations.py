"""config resource operations; transaction and failure semantics belong to the owning capability."""

from infrastructure.request_errors import RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.snapshots import AccountIdentity
from features.configuration.desktop_config import flatten_config, settings_to_config
from features.configuration.schemas import DesktopConfigPutRequest, DesktopConfigResponse
from features.configuration.settings.values import load_user_settings, put_user_settings


async def get_config(user: AccountIdentity, db: AsyncSession) -> DesktopConfigResponse:
    values = {"companion.information_discovery_enabled": False} | await load_user_settings(db, user.id)
    return DesktopConfigResponse(config=settings_to_config(values))


async def put_config(body: DesktopConfigPutRequest, user: AccountIdentity, db: AsyncSession) -> DesktopConfigResponse:
    values = await load_user_settings(db, user.id)
    updates = flatten_config(body.config)
    if "companion.information_discovery_enabled" in updates and not isinstance(
        updates["companion.information_discovery_enabled"],
        bool,
    ):
        raise RequestError(status_code=422, detail="主动信息检索开关必须为布尔值")
    await put_user_settings(db, user.id, updates)
    await db.commit()
    return DesktopConfigResponse(
        config=settings_to_config({"companion.information_discovery_enabled": False} | values | updates),
    )
