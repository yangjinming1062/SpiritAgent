import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from components import SESSION_LOCAL, SETTINGS
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import AdminSession

# 激活 token 是不可猜随机串（非用户密码），SHA-256 已够，不需要慢哈希。
ACTIVATION_TOKEN_BYTES = 32
BEARER_SCHEME = HTTPBearer(auto_error=False)
WS_TICKET_TTL_SECONDS = 60


@dataclass(frozen=True)
class _PendingWsTicket:
    jti: str
    expires_at: float


# WS 本身按单 web 进程部署；每个登录只保留最新待握手票据，签发与消费没有 await。
_pending_ws_tickets: dict[int, _PendingWsTicket] = {}
_next_ws_ticket_prune = 0.0


def _to_urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_activation_token() -> str:
    return secrets.token_urlsafe(ACTIVATION_TOKEN_BYTES)


def hash_activation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_activation_code(base_url: str, token: str) -> str:
    """把 ``{baseUrl, token}`` 打包成不透明 base64url 串；客户端解码拿地址与 token。"""
    payload = json.dumps({"b": base_url, "t": token}, separators=(",", ":"))
    return _to_urlsafe_b64(payload.encode("utf-8"))


def decode_activation_code(code: str) -> tuple[str, str]:
    """encode_activation_code 的反向操作；格式错误抛 ValueError。"""
    raw = _b64decode(code)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("activation code payload must be a JSON object")
    base_url = data.get("b")
    token = data.get("t")
    if not isinstance(base_url, str) or not isinstance(token, str) or not base_url or not token:
        raise ValueError("activation code missing required fields")
    return base_url, token


def create_access_token(
    *,
    user_id: int,
    username: str,
    client_context: dict | None = None,
    expires_in_seconds: int | None = None,
    purpose: str | None = None,
    login_record_id: int | None = None,
) -> tuple[str, int, str]:
    token_jti = uuid4().hex
    expires_delta = (
        timedelta(minutes=SETTINGS.access_token_expire_minutes)
        if expires_in_seconds is None
        else timedelta(seconds=expires_in_seconds)
    )
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": str(user_id), "username": username, "jti": token_jti, "exp": expires_at}
    if client_context:
        payload["ctx"] = client_context
    if purpose:
        payload["purpose"] = purpose
    if login_record_id is not None:
        payload["login_id"] = login_record_id
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    return token, int(expires_delta.total_seconds()), token_jti


async def create_admin_token() -> tuple[str, int]:
    jti = uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": "admin", "username": SETTINGS.admin_username, "is_admin": True, "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    # deps.get_current_admin_token 要求 jti 行已存在，insert 失败必须抛出而非 mint 首调就 401 的 token。
    async with SESSION_LOCAL() as db:
        db.add(AdminSession(token_jti=jti, username=SETTINGS.admin_username, is_active=True))
        await db.commit()
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])


def create_ws_ticket(*, user_id: int, username: str, login_record_id: int) -> tuple[str, int]:
    global _next_ws_ticket_prune
    now = time.monotonic()
    if now >= _next_ws_ticket_prune:
        expired = [key for key, ticket in _pending_ws_tickets.items() if ticket.expires_at <= now]
        for key in expired:
            del _pending_ws_tickets[key]
        _next_ws_ticket_prune = now + WS_TICKET_TTL_SECONDS
    token, expires_in, jti = create_access_token(
        user_id=user_id,
        username=username,
        expires_in_seconds=WS_TICKET_TTL_SECONDS,
        purpose="ws",
        login_record_id=login_record_id,
    )
    _pending_ws_tickets[login_record_id] = _PendingWsTicket(jti=jti, expires_at=now + expires_in)
    return token, expires_in


def consume_ws_ticket(token: str) -> bool:
    """签名与用途正确且仍待握手的票据只允许消费一次，失败后客户端须重新签发。"""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return False
    if payload.get("purpose") != "ws":
        return False
    login_id = payload.get("login_id")
    if not isinstance(login_id, int) or isinstance(login_id, bool):
        return False
    pending = _pending_ws_tickets.get(login_id)
    if pending is None or pending.jti != payload.get("jti") or pending.expires_at <= time.monotonic():
        return False
    del _pending_ws_tickets[login_id]
    return True


def decode_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌。")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效。") from exc
