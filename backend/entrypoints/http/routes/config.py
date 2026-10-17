import features.configuration.desktop_operations as desktop_configuration
from features.companion.interaction_stats import invalidate_user_timezone
from features.configuration.schemas import DesktopConfigPutRequest, DesktopConfigResponse
from infrastructure.database import DbSession

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router()


@router.get("", response_model=DesktopConfigResponse)
async def get_config(user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    return await desktop_configuration.get_config(user=user, db=db)


@router.put("", response_model=DesktopConfigResponse)
async def put_config(body: DesktopConfigPutRequest, user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    result = await desktop_configuration.put_config(body=body, user=user, db=db)
    if "timezone" in body.config:
        invalidate_user_timezone(user.id)
    return result
