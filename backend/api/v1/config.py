from common import get_router
from components import DbSession
from fastapi import Depends
from modules.auth import CurrentUser, get_current_session
from modules.settings import UserSetting
from modules.system import DesktopConfigPutRequest, DesktopConfigResponse
from services.domains.configuration.desktop_config import DEFAULT_CONFIG, flatten_config, settings_to_config
from sqlalchemy import select

router = get_router()


@router.get("", response_model=DesktopConfigResponse)
async def get_config(user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    settings = (await db.execute(select(UserSetting).where(UserSetting.user_id == user.id))).scalars().all()
    return DesktopConfigResponse(config=settings_to_config(settings))


@router.put("", response_model=DesktopConfigResponse)
async def put_config(body: DesktopConfigPutRequest, user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    pairs = flatten_config(body.config)
    for key, value in pairs:
        setting = (
            await db.execute(select(UserSetting).where(UserSetting.user_id == user.id, UserSetting.setting_key == key))
        ).scalar_one_or_none()
        if setting:
            setting.setting_value = value
        else:
            db.add(UserSetting(user_id=user.id, setting_key=key, setting_value=value))

    await db.commit()

    settings = (await db.execute(select(UserSetting).where(UserSetting.user_id == user.id))).scalars().all()
    return DesktopConfigResponse(config=settings_to_config(settings))


@router.get("/defaults", response_model=DesktopConfigResponse, dependencies=[Depends(get_current_session)])
async def get_config_defaults() -> DesktopConfigResponse:
    return DesktopConfigResponse(config=DEFAULT_CONFIG)
