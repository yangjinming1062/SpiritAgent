from dataclasses import dataclass
from datetime import timedelta

from infrastructure.request_errors import RequestError
from infrastructure.text import ensure_utc, utc_now
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.accounts.models import User
from features.accounts.remote.models import RemotePairing, RemoteSession
from features.accounts.remote.schemas import (
    DeviceListResponse,
    DeviceResponse,
    ExchangeRequest,
    PairingResponse,
    RemoteSessionResponse,
    RemoteUserResponse,
)
from features.accounts.remote.security import (
    PAIRING_TTL_SECONDS,
    REMOTE_SESSION_TTL_SECONDS,
    cancel_pending_pairings,
    hash_remote_token,
    new_remote_token,
    remote_csrf_token,
    remote_public_base_url,
    revoke_remote_grants,
)
from features.accounts.snapshots import RemoteDeviceIdentity, remote_identity


@dataclass(frozen=True, slots=True)
class RemoteExchangeResult:
    response: RemoteSessionResponse
    token: str
    previous_device: tuple[int, int] | None


def _pairing_response(pairing: RemotePairing, url: str | None = None) -> PairingResponse:
    state = "pending"
    if pairing.consumed_at is not None:
        state = "paired"
    elif pairing.cancelled_at is not None:
        state = "cancelled"
    elif ensure_utc(pairing.expires_at) <= utc_now():
        state = "expired"
    return PairingResponse(id=pairing.id, url=url, expires_at=pairing.expires_at, state=state)


def session_response(user_id: int, username: str, device: RemoteDeviceIdentity, token: str) -> RemoteSessionResponse:
    return RemoteSessionResponse(
        user=RemoteUserResponse(id=user_id, username=username),
        device=DeviceResponse.model_validate(device),
        csrf_token=remote_csrf_token(token),
    )


async def create_pairing(user_id: int, db: AsyncSession) -> PairingResponse:
    base_url = remote_public_base_url()
    active_user_id = await db.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update(),
    )
    if active_user_id is None:
        raise RequestError(status_code=401, message="账户已停用。")
    await cancel_pending_pairings(db, user_id)
    token = new_remote_token()
    pairing = RemotePairing(
        user_id=user_id,
        token_hash=hash_remote_token(token),
        expires_at=utc_now() + timedelta(seconds=PAIRING_TTL_SECONDS),
    )
    db.add(pairing)
    await db.commit()
    return _pairing_response(pairing, f"{base_url}/remote/#token={token}")


async def pairing_status(pairing_id: int, user_id: int, db: AsyncSession) -> PairingResponse:
    pairing = await db.scalar(
        select(RemotePairing).where(RemotePairing.id == pairing_id, RemotePairing.user_id == user_id),
    )
    if pairing is None:
        raise RequestError(status_code=404, message="二维码不存在。")
    return _pairing_response(pairing)


async def cancel_pairing(pairing_id: int, user_id: int, db: AsyncSession) -> None:
    pairing = await db.scalar(
        select(RemotePairing).where(RemotePairing.id == pairing_id, RemotePairing.user_id == user_id).with_for_update(),
    )
    if pairing is None:
        raise RequestError(status_code=404, message="二维码不存在。")
    if pairing.consumed_at is None:
        pairing.cancelled_at = utc_now()
    await db.commit()


async def exchange_pairing(payload: ExchangeRequest, db: AsyncSession, old_token: str | None) -> RemoteExchangeResult:
    digest = hash_remote_token(payload.token)
    user_id = await db.scalar(select(RemotePairing.user_id).where(RemotePairing.token_hash == digest))
    if user_id is None:
        raise RequestError(status_code=401, message="二维码无效，请重新连接。")
    # 与二维码刷新、凭据重置共用用户锁；对待兑换码再取行锁。
    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update(),
    )
    if is_user_in_maintenance(user_id):
        raise RequestError(status_code=503, message="用户数据正在维护，请稍后重试。")
    pairing = await db.scalar(select(RemotePairing).where(RemotePairing.token_hash == digest).with_for_update())
    if (
        user is None
        or pairing is None
        or pairing.consumed_at is not None
        or pairing.cancelled_at is not None
        or ensure_utc(pairing.expires_at) <= utc_now()
    ):
        raise RequestError(status_code=401, message="二维码已失效，请重新连接。")
    pairing.consumed_at = utc_now()
    token = new_remote_token()
    device = RemoteSession(
        user_id=user_id,
        token_hash=hash_remote_token(token),
        name=payload.device_name.strip() or "手机浏览器",
        expires_at=utc_now() + timedelta(seconds=REMOTE_SESSION_TTL_SECONDS),
        last_seen_at=utc_now(),
    )
    db.add(device)
    old_device = None
    if old_token and len(old_token) <= 128:
        old_device = await db.scalar(
            select(RemoteSession).where(RemoteSession.token_hash == hash_remote_token(old_token)),
        )
        if old_device is not None:
            await revoke_remote_grants(db, old_device.user_id, old_device.id)
    await db.commit()
    previous_device = (old_device.user_id, old_device.id) if old_device is not None else None
    result = session_response(user_id, user.username, remote_identity(device), token)
    return RemoteExchangeResult(result, token, previous_device)


async def logout_remote_session(user_id: int, device_id: int, db: AsyncSession) -> None:
    await revoke_remote_grants(db, user_id, device_id)
    await db.commit()


async def list_remote_devices(user_id: int, db: AsyncSession) -> DeviceListResponse:
    devices = (
        await db.scalars(
            select(RemoteSession)
            .where(
                RemoteSession.user_id == user_id,
                RemoteSession.revoked_at.is_(None),
                RemoteSession.expires_at > utc_now(),
            )
            .order_by(RemoteSession.last_seen_at.desc(), RemoteSession.id.desc()),
        )
    ).all()
    return DeviceListResponse(items=[DeviceResponse.model_validate(device) for device in devices])


async def revoke_remote_device(device_id: int, user_id: int, db: AsyncSession) -> None:
    if (
        await db.scalar(select(RemoteSession.id).where(RemoteSession.id == device_id, RemoteSession.user_id == user_id))
        is None
    ):
        raise RequestError(status_code=404, message="设备不存在。")
    await revoke_remote_grants(db, user_id, device_id)
    await db.commit()


async def revoke_all_remote_devices(user_id: int, db: AsyncSession) -> None:
    await lock_account(db, user_id)
    await revoke_remote_grants(db, user_id)
    await db.commit()
