import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from infrastructure.config import SETTINGS
from infrastructure.hashing import sha256_hex
from infrastructure.text import utc_now
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.remote.models import RemotePairing, RemoteSession

REMOTE_COOKIE = "__Host-spirit_remote"
PAIRING_TTL_SECONDS = 5 * 60
REMOTE_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def hash_remote_token(token: str) -> str:
    return sha256_hex(token)


def new_remote_token() -> str:
    return secrets.token_urlsafe(32)


def remote_csrf_token(token: str) -> str:
    return hmac.new(SETTINGS.jwt_secret_key.encode(), f"remote-csrf:{token}".encode(), hashlib.sha256).hexdigest()


def remote_public_base_url() -> str:
    value = SETTINGS.public_base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="公网 HTTPS 地址格式无效。") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise HTTPException(status_code=503, detail="请先配置可供手机访问的公网 HTTPS 地址（PUBLIC_BASE_URL）。")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=503, detail="远程连接需要手机可访问的公网 HTTPS 地址。")
    try:
        hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(status_code=503, detail="公网 HTTPS 地址格式无效。") from exc
    return f"https://{hostname}" + (f":{port}" if port not in {None, 443} else "")


def require_remote_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = remote_public_base_url()
    if origin != expected:
        raise HTTPException(status_code=403, detail="远程请求来源无效。")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        raise HTTPException(status_code=403, detail="仅允许页面同源请求。")


async def cancel_pending_pairings(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(RemotePairing)
        .where(
            RemotePairing.user_id == user_id,
            RemotePairing.cancelled_at.is_(None),
            RemotePairing.consumed_at.is_(None),
        )
        .values(cancelled_at=utc_now()),
    )


async def revoke_remote_grants(db: AsyncSession, user_id: int, device_id: int | None = None) -> None:
    query = update(RemoteSession).where(RemoteSession.user_id == user_id, RemoteSession.revoked_at.is_(None))
    if device_id is not None:
        query = query.where(RemoteSession.id == device_id)
    await db.execute(query.values(revoked_at=utc_now()))
    if device_id is None:
        await cancel_pending_pairings(db, user_id)
