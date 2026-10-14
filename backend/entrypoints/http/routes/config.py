import features.configuration.desktop_operations as desktop_configuration
from fastapi import HTTPException
from features.configuration.schemas import DesktopConfigPutRequest, DesktopConfigResponse
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError

from entrypoints.http.auth import CurrentUser
from entrypoints.http.routing import get_router

router = get_router()


@router.get("", response_model=DesktopConfigResponse)
async def get_config(user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    try:
        result = await desktop_configuration.get_config(user=user, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result


@router.put("", response_model=DesktopConfigResponse)
async def put_config(body: DesktopConfigPutRequest, user: CurrentUser, db: DbSession) -> DesktopConfigResponse:
    try:
        result = await desktop_configuration.put_config(body=body, user=user, db=db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return result
