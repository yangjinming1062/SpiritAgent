from fastapi import Depends, HTTPException, Request, Response, WebSocket
from features.accounts.remote import use_cases as remote_use_cases
from features.accounts.remote.schemas import (
    DeviceListResponse,
    DeviceResponse,
    ExchangeRequest,
    PairingResponse,
    RemoteSessionResponse,
    RemoteTicketResponse,
    RemoteUserResponse,
)
from features.accounts.remote.security import (
    REMOTE_COOKIE,
    REMOTE_SESSION_TTL_SECONDS,
    RemoteIdentity,
    create_remote_ws_ticket,
    remote_csrf_token,
    remote_public_base_url,
    require_remote_origin,
)
from features.accounts.snapshots import AccountIdentity, RemoteDeviceIdentity
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError
from runtime.channels import terminate_remote_sessions
from runtime.responses import MessageResponse
from slowapi.util import get_remote_address

from entrypoints.desktop.handlers import handle_remote_websocket
from entrypoints.http.auth import CurrentRemoteSession, CurrentSession
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router


async def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = get_router(dependencies=[Depends(_private_response)])


def _session_response(user: AccountIdentity, device: RemoteDeviceIdentity, token: str) -> RemoteSessionResponse:
    return RemoteSessionResponse(
        user=RemoteUserResponse(id=user.id, username=user.username),
        device=DeviceResponse.model_validate(device),
        csrf_token=remote_csrf_token(token),
    )


@router.post("/pairings", response_model=PairingResponse, response_model_exclude_none=True)
async def create_pairing(session: CurrentSession, db: DbSession) -> PairingResponse:
    try:
        return await remote_use_cases.create_pairing(session[0].id, db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/pairings/{pairing_id}", response_model=PairingResponse, response_model_exclude_none=True)
async def pairing_status(pairing_id: int, session: CurrentSession, db: DbSession) -> PairingResponse:
    try:
        return await remote_use_cases.pairing_status(pairing_id, session[0].id, db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/pairings/{pairing_id}", response_model=MessageResponse)
async def cancel_pairing(pairing_id: int, session: CurrentSession, db: DbSession) -> MessageResponse:
    try:
        await remote_use_cases.cancel_pairing(pairing_id, session[0].id, db)
        return MessageResponse(message="二维码已取消。")
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/session/exchange", response_model=RemoteSessionResponse)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def exchange_pairing(
    payload: ExchangeRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> RemoteSessionResponse:
    try:
        require_remote_origin(request)
        exchange = await remote_use_cases.exchange_pairing(payload, db, request.cookies.get(REMOTE_COOKIE))
        if exchange.previous_device is not None:
            await terminate_remote_sessions(*exchange.previous_device)
        response.set_cookie(
            REMOTE_COOKIE,
            exchange.token,
            max_age=REMOTE_SESSION_TTL_SECONDS,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return exchange.response
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/session", response_model=RemoteSessionResponse)
async def current_remote_session(request: Request, session: CurrentRemoteSession) -> RemoteSessionResponse:
    return _session_response(*session, request.cookies[REMOTE_COOKIE])


@router.delete("/session", response_model=MessageResponse)
async def logout_remote_session(response: Response, session: CurrentRemoteSession, db: DbSession) -> MessageResponse:
    try:
        user, device = session
        await remote_use_cases.logout_remote_session(user.id, device.id, db)
        await terminate_remote_sessions(user.id, device.id)
        response.delete_cookie(REMOTE_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return MessageResponse(message="已退出登录。")
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/devices", response_model=DeviceListResponse)
async def list_remote_devices(session: CurrentSession, db: DbSession) -> DeviceListResponse:
    try:
        return await remote_use_cases.list_remote_devices(session[0].id, db)
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/devices/{device_id}", response_model=MessageResponse)
async def revoke_remote_device(device_id: int, session: CurrentSession, db: DbSession) -> MessageResponse:
    try:
        await remote_use_cases.revoke_remote_device(device_id, session[0].id, db)
        await terminate_remote_sessions(session[0].id, device_id)
        return MessageResponse(message="设备授权已撤销。")
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/devices", response_model=MessageResponse)
async def revoke_all_remote_devices(session: CurrentSession, db: DbSession) -> MessageResponse:
    try:
        await remote_use_cases.revoke_all_remote_devices(session[0].id, db)
        await terminate_remote_sessions(session[0].id)
        return MessageResponse(message="全部设备授权已撤销。")
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/ws-ticket", response_model=RemoteTicketResponse)
async def mint_remote_ticket(session: CurrentRemoteSession) -> RemoteTicketResponse:
    user, device = session
    token, expires_in = create_remote_ws_ticket(RemoteIdentity(user_id=user.id, device_id=device.id))
    return RemoteTicketResponse(ticket=token, expires_in=expires_in)


@router.websocket("/ws")
async def remote_websocket(websocket: WebSocket, ticket: str = "") -> None:
    if websocket.headers.get("origin") != remote_public_base_url():
        await websocket.close(code=4403, reason="远程请求来源无效")
        return
    await handle_remote_websocket(websocket, ticket)
