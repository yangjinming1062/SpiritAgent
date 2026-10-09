import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import jwt
from components import SETTINGS, utc_now
from fastapi import HTTPException, Request
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import RemotePairing, RemoteSession

REMOTE_COOKIE = "__Host-spirit_remote"
PAIRING_TTL_SECONDS = 5 * 60
REMOTE_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
REMOTE_WS_TICKET_TTL_SECONDS = 60


@dataclass(frozen=True)
class RemoteIdentity:
    user_id: int
    device_id: int


_pending_tickets: dict[str, tuple[RemoteIdentity, float]] = {}


def hash_remote_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def remote_content_allowed(path: str, method: str) -> bool:
    path = path.rstrip("/")
    if path == "/api/sessions" or path.startswith("/api/sessions/"):
        return True
    if any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in (
            "/api/companion/posts",
            "/api/companion/diary",
            "/api/companion/outfits",
            "/api/companion/scenes",
            "/api/companion/video-packs",
        )
    ):
        return True
    if path in {"/api/media/stt", "/api/media/tts", "/api/media/videos"}:
        return method == "POST"
    return method == "GET" and (
        path in {"/api/companion/persona", "/api/companion/avatar", "/api/companion/character-card"}
        or path.startswith("/api/companion/asset/")
    )


def create_remote_ws_ticket(identity: RemoteIdentity) -> tuple[str, int]:
    now = time.monotonic()
    # 同设备只保留最新待握手票据，同时清理过期票据。
    for nonce in [key for key, (owner, expiry) in _pending_tickets.items() if expiry <= now or owner == identity]:
        del _pending_tickets[nonce]
    nonce = uuid4().hex
    _pending_tickets[nonce] = (identity, now + REMOTE_WS_TICKET_TTL_SECONDS)
    token = jwt.encode(
        {
            "sub": str(identity.user_id),
            "device_id": identity.device_id,
            "purpose": "remote_ws",
            "jti": nonce,
            "exp": utc_now() + timedelta(seconds=REMOTE_WS_TICKET_TTL_SECONDS),
        },
        SETTINGS.jwt_secret_key,
        algorithm=SETTINGS.jwt_algorithm,
    )
    return token, REMOTE_WS_TICKET_TTL_SECONDS


def consume_remote_ws_ticket(token: str) -> RemoteIdentity | None:
    try:
        payload = jwt.decode(token, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    sub, device_id, nonce = payload.get("sub"), payload.get("device_id"), payload.get("jti")
    if (
        payload.get("purpose") != "remote_ws"
        or not isinstance(sub, str)
        or not sub.isdigit()
        or type(device_id) is not int
        or not isinstance(nonce, str)
    ):
        return None
    identity = RemoteIdentity(user_id=int(sub), device_id=device_id)
    pending = _pending_tickets.get(nonce)
    if pending is None or pending[0] != identity or pending[1] <= time.monotonic():
        return None
    del _pending_tickets[nonce]
    return identity


async def revoke_remote_grants(db: AsyncSession, user_id: int, device_id: int | None = None) -> None:
    query = update(RemoteSession).where(RemoteSession.user_id == user_id, RemoteSession.revoked_at.is_(None))
    if device_id is not None:
        query = query.where(RemoteSession.id == device_id)
    await db.execute(query.values(revoked_at=utc_now()))
    if device_id is None:
        await db.execute(
            update(RemotePairing)
            .where(
                RemotePairing.user_id == user_id,
                RemotePairing.cancelled_at.is_(None),
                RemotePairing.consumed_at.is_(None),
            )
            .values(cancelled_at=utc_now()),
        )
    for nonce in [
        key
        for key, (owner, _) in _pending_tickets.items()
        if owner.user_id == user_id and (device_id is None or owner.device_id == device_id)
    ]:
        del _pending_tickets[nonce]
