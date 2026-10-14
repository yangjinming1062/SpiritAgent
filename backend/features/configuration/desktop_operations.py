"""config resource operations; transaction and failure semantics belong to the owning capability."""

from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.snapshots import AccountIdentity
from features.configuration.desktop_config import flatten_config, settings_to_config
from features.configuration.schemas import DesktopConfigPutRequest, DesktopConfigResponse
from features.configuration.settings.values import load_user_settings, put_user_settings


async def get_config(user: AccountIdentity, db: AsyncSession) -> DesktopConfigResponse:
    return DesktopConfigResponse(config=settings_to_config(await load_user_settings(db, user.id)))


async def put_config(body: DesktopConfigPutRequest, user: AccountIdentity, db: AsyncSession) -> DesktopConfigResponse:
    values = await load_user_settings(db, user.id)
    updates = flatten_config(body.config)
    await put_user_settings(db, user.id, updates)
    await db.commit()
    return DesktopConfigResponse(config=settings_to_config(values | updates))
