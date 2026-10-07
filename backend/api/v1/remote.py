from datetime import timedelta

from common import get_router
from components import SETTINGS, DbSession, ensure_utc, is_user_in_maintenance, utc_now
from fastapi import Depends, HTTPException, Request, Response, WebSocket
from modules.auth import CurrentRemoteSession, CurrentSession, User, lock_user_row
from modules.remote import (
    PAIRING_TTL_SECONDS,
    REMOTE_COOKIE,
    REMOTE_SESSION_TTL_SECONDS,
    DeviceListResponse,
    DeviceResponse,
    ExchangeRequest,
    PairingResponse,
    RemoteIdentity,
    RemotePairing,
    RemoteSession,
    RemoteSessionResponse,
    RemoteTicketResponse,
    RemoteUserResponse,
    create_remote_ws_ticket,
    hash_remote_token,
    new_remote_token,
    remote_csrf_token,
    remote_public_base_url,
    require_remote_origin,
    revoke_remote_grants,
)
from modules.system import MessageResponse
from services.adapters.desktop import handle_remote_websocket, terminate_remote_sessions
from services.adapters.http import limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update


async def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = get_router(dependencies=[Depends(_private_response)])


def _pairing_response(pairing: RemotePairing, url: str | None = None) -> PairingResponse:
    state = "pending"
    if pairing.consumed_at is not None:
        state = "paired"
    elif pairing.cancelled_at is not None:
        state = "cancelled"
    elif ensure_utc(pairing.expires_at) <= utc_now():
        state = "expired"
    return PairingResponse(id=pairing.id, url=url, expires_at=pairing.expires_at, state=state)


def _session_response(user: User, device: RemoteSession, token: str) -> RemoteSessionResponse:
    return RemoteSessionResponse(
        user=RemoteUserResponse(id=user.id, username=user.username),
        device=DeviceResponse.model_validate(device),
        csrf_token=remote_csrf_token(token),
    )


@router.post("/pairings", response_model=PairingResponse, response_model_exclude_none=True)
async def create_pairing(session: CurrentSession, db: DbSession) -> PairingResponse:
    user = session[0]
    base_url = remote_public_base_url()
    active_user_id = await db.scalar(
        select(User.id).where(User.id == user.id, User.is_active.is_(True)).with_for_update(),
    )
    if active_user_id is None:
        raise HTTPException(status_code=401, detail="账户已停用。")
    await db.execute(
        update(RemotePairing)
        .where(
            RemotePairing.user_id == user.id,
            RemotePairing.consumed_at.is_(None),
            RemotePairing.cancelled_at.is_(None),
        )
        .values(cancelled_at=utc_now()),
    )
    token = new_remote_token()
    pairing = RemotePairing(
        user_id=user.id,
        token_hash=hash_remote_token(token),
        expires_at=utc_now() + timedelta(seconds=PAIRING_TTL_SECONDS),
    )
    db.add(pairing)
    await db.commit()
    return _pairing_response(pairing, f"{base_url}/remote/#token={token}")


@router.get("/pairings/{pairing_id}", response_model=PairingResponse, response_model_exclude_none=True)
async def pairing_status(pairing_id: int, session: CurrentSession, db: DbSession) -> PairingResponse:
    pairing = await db.scalar(
        select(RemotePairing).where(RemotePairing.id == pairing_id, RemotePairing.user_id == session[0].id),
    )
    if pairing is None:
        raise HTTPException(status_code=404, detail="二维码不存在。")
    return _pairing_response(pairing)


@router.delete("/pairings/{pairing_id}", response_model=MessageResponse)
async def cancel_pairing(pairing_id: int, session: CurrentSession, db: DbSession) -> MessageResponse:
    pairing = await db.scalar(
        select(RemotePairing)
        .where(RemotePairing.id == pairing_id, RemotePairing.user_id == session[0].id)
        .with_for_update(),
    )
    if pairing is None:
        raise HTTPException(status_code=404, detail="二维码不存在。")
    if pairing.consumed_at is None:
        pairing.cancelled_at = utc_now()
    await db.commit()
    return MessageResponse(message="二维码已取消。")


@router.post("/session/exchange", response_model=RemoteSessionResponse)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def exchange_pairing(
    payload: ExchangeRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> RemoteSessionResponse:
    require_remote_origin(request)
    digest = hash_remote_token(payload.token)
    user_id = await db.scalar(select(RemotePairing.user_id).where(RemotePairing.token_hash == digest))
    if user_id is None:
        raise HTTPException(status_code=401, detail="二维码无效，请重新连接。")
    # 与二维码刷新、凭据重置共用用户锁；对待兑换码再取行锁。
    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update(),
    )
    if is_user_in_maintenance(user_id):
        raise HTTPException(status_code=503, detail="用户数据正在维护，请稍后重试。")
    pairing = await db.scalar(select(RemotePairing).where(RemotePairing.token_hash == digest).with_for_update())
    if (
        user is None
        or pairing is None
        or pairing.consumed_at is not None
        or pairing.cancelled_at is not None
        or ensure_utc(pairing.expires_at) <= utc_now()
    ):
        raise HTTPException(status_code=401, detail="二维码已失效，请重新连接。")
    pairing.consumed_at = utc_now()
    token = new_remote_token()
    device = RemoteSession(
        user_id=user.id,
        token_hash=hash_remote_token(token),
        name=payload.device_name.strip() or "手机浏览器",
        expires_at=utc_now() + timedelta(seconds=REMOTE_SESSION_TTL_SECONDS),
        last_seen_at=utc_now(),
    )
    db.add(device)
    old_token = request.cookies.get(REMOTE_COOKIE)
    old_device = None
    if old_token and len(old_token) <= 128:
        old_device = await db.scalar(
            select(RemoteSession).where(RemoteSession.token_hash == hash_remote_token(old_token)),
        )
        if old_device is not None:
            await revoke_remote_grants(db, old_device.user_id, old_device.id)
    await db.commit()
    if old_device is not None:
        await terminate_remote_sessions(old_device.user_id, old_device.id)
    response.set_cookie(
        REMOTE_COOKIE,
        token,
        max_age=REMOTE_SESSION_TTL_SECONDS,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return _session_response(user, device, token)


@router.get("/session", response_model=RemoteSessionResponse)
async def current_remote_session(request: Request, session: CurrentRemoteSession) -> RemoteSessionResponse:
    return _session_response(*session, request.cookies[REMOTE_COOKIE])


@router.delete("/session", response_model=MessageResponse)
async def logout_remote_session(response: Response, session: CurrentRemoteSession, db: DbSession) -> MessageResponse:
    user, device = session
    await revoke_remote_grants(db, user.id, device.id)
    await db.commit()
    await terminate_remote_sessions(user.id, device.id)
    response.delete_cookie(REMOTE_COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    return MessageResponse(message="已退出登录。")


@router.get("/devices", response_model=DeviceListResponse)
async def list_remote_devices(session: CurrentSession, db: DbSession) -> DeviceListResponse:
    devices = (
        await db.scalars(
            select(RemoteSession)
            .where(
                RemoteSession.user_id == session[0].id,
                RemoteSession.revoked_at.is_(None),
                RemoteSession.expires_at > utc_now(),
            )
            .order_by(RemoteSession.last_seen_at.desc(), RemoteSession.id.desc()),
        )
    ).all()
    return DeviceListResponse(items=[DeviceResponse.model_validate(device) for device in devices])


@router.delete("/devices/{device_id}", response_model=MessageResponse)
async def revoke_remote_device(device_id: int, session: CurrentSession, db: DbSession) -> MessageResponse:
    user_id = session[0].id
    if (
        await db.scalar(select(RemoteSession.id).where(RemoteSession.id == device_id, RemoteSession.user_id == user_id))
        is None
    ):
        raise HTTPException(status_code=404, detail="设备不存在。")
    await revoke_remote_grants(db, user_id, device_id)
    await db.commit()
    await terminate_remote_sessions(user_id, device_id)
    return MessageResponse(message="设备授权已撤销。")


@router.delete("/devices", response_model=MessageResponse)
async def revoke_all_remote_devices(session: CurrentSession, db: DbSession) -> MessageResponse:
    user_id = session[0].id
    await lock_user_row(db, user_id)
    await revoke_remote_grants(db, user_id)
    await db.commit()
    await terminate_remote_sessions(user_id)
    return MessageResponse(message="全部设备授权已撤销。")


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
