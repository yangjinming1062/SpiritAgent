from common import get_router
from components import DbSession
from modules.auth import CurrentUser
from modules.settings import load_user_settings, put_user_settings
from modules.system import DesktopConfigPutRequest, DesktopConfigResponse
from services.domains.configuration import flatten_config, settings_to_config

router = get_router()


@router.get("", response_model=DesktopConfigResponse)
async def get_config(user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    return DesktopConfigResponse(config=settings_to_config(await load_user_settings(db, user.id)))


@router.put("", response_model=DesktopConfigResponse)
async def put_config(body: DesktopConfigPutRequest, user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    values = await load_user_settings(db, user.id)
    updates = flatten_config(body.config)
    await put_user_settings(db, user.id, updates)
    await db.commit()
    return DesktopConfigResponse(config=settings_to_config(values | updates))
