from fastapi import Depends, Request, Response
from features.accounts.remote import use_cases as remote_use_cases
from features.accounts.remote.schemas import (
    DeviceListResponse,
    ExchangeRequest,
    PairingResponse,
    RemoteSessionResponse,
)
from features.accounts.remote.security import (
    REMOTE_SESSION_TTL_SECONDS,
    remote_session_cookie,
    require_remote_origin,
)
from infrastructure.api_ids import ResourceId
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from runtime.channels import terminate_remote_sessions
from runtime.responses import MessageResponse
from slowapi.util import get_remote_address

from entrypoints.http.auth import CurrentRemoteSession, CurrentSession
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router


async def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = get_router(dependencies=[Depends(_private_response)])


@router.post("/pairings", response_model=PairingResponse)
async def create_pairing(session: CurrentSession, db: DbSession) -> PairingResponse:
    return await remote_use_cases.create_pairing(session[0].id, db)


@router.get("/pairings/{pairing_id}", response_model=PairingResponse)
async def pairing_status(pairing_id: ResourceId, session: CurrentSession, db: DbSession) -> PairingResponse:
    return await remote_use_cases.pairing_status(pairing_id, session[0].id, db)


@router.delete("/pairings/{pairing_id}", response_model=MessageResponse)
async def cancel_pairing(pairing_id: ResourceId, session: CurrentSession, db: DbSession) -> MessageResponse:
    await remote_use_cases.cancel_pairing(pairing_id, session[0].id, db)
    return MessageResponse(message="二维码已取消。")


@router.post(
    "/session/exchange", response_model=RemoteSessionResponse, openapi_extra=operation_policy(identities=("remote",))
)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def exchange_pairing(
    payload: ExchangeRequest, request: Request, response: Response, db: DbSession
) -> RemoteSessionResponse:
    require_remote_origin(request)
    cookie = remote_session_cookie()
    exchange = await remote_use_cases.exchange_pairing(payload, db, request.cookies.get(cookie.name))
    if exchange.previous_device is not None:
        await terminate_remote_sessions(*exchange.previous_device)
    response.set_cookie(
        cookie.name,
        exchange.token,
        max_age=REMOTE_SESSION_TTL_SECONDS,
        secure=cookie.secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return exchange.response


@router.get("/session", response_model=RemoteSessionResponse, openapi_extra=operation_policy(identities=("remote",)))
async def current_remote_session(request: Request, session: CurrentRemoteSession) -> RemoteSessionResponse:
    user, device = session
    return remote_use_cases.session_response(
        user.id, user.username, device, request.cookies[remote_session_cookie().name]
    )


@router.delete("/session", response_model=MessageResponse, openapi_extra=operation_policy(identities=("remote",)))
async def logout_remote_session(response: Response, session: CurrentRemoteSession, db: DbSession) -> MessageResponse:
    user, device = session
    await remote_use_cases.logout_remote_session(user.id, device.id, db)
    await terminate_remote_sessions(user.id, device.id)
    cookie = remote_session_cookie()
    response.delete_cookie(cookie.name, secure=cookie.secure, httponly=True, samesite="strict", path="/")
    return MessageResponse(message="已退出登录。")


@router.get("/devices", response_model=DeviceListResponse)
async def list_remote_devices(session: CurrentSession, db: DbSession) -> DeviceListResponse:
    return await remote_use_cases.list_remote_devices(session[0].id, db)


@router.delete("/devices/{device_id}", response_model=MessageResponse)
async def revoke_remote_device(device_id: ResourceId, session: CurrentSession, db: DbSession) -> MessageResponse:
    await remote_use_cases.revoke_remote_device(device_id, session[0].id, db)
    await terminate_remote_sessions(session[0].id, device_id)
    return MessageResponse(message="设备授权已撤销。")


@router.delete("/devices", response_model=MessageResponse)
async def revoke_all_remote_devices(session: CurrentSession, db: DbSession) -> MessageResponse:
    await remote_use_cases.revoke_all_remote_devices(session[0].id, db)
    await terminate_remote_sessions(session[0].id)
    return MessageResponse(message="全部设备授权已撤销。")
