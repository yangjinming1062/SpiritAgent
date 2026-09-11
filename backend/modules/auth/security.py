import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from components import SESSION_LOCAL, SETTINGS
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .models import AdminSession

# 激活 token 是不可猜随机串（非用户密码），SHA-256 已够，不需要 PBKDF2 慢哈希。
ACTIVATION_TOKEN_BYTES = 32
BEARER_SCHEME = HTTPBearer(auto_error=False)


def _to_urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_activation_token() -> str:
    """生成 URL-safe 随机激活 token（~43 字符）。"""
    return secrets.token_urlsafe(ACTIVATION_TOKEN_BYTES)


def hash_activation_token(token: str) -> str:
    """激活 token 的 SHA-256 摘要，用于 DB 存储与查找。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_activation_code(base_url: str, token: str) -> str:
    """把 ``{baseUrl, token}`` 打包成一个不透明的 base64url 串，对用户呈现为乱码；客户端解码以拿到后端地址与激活 token。"""
    payload = json.dumps({"b": base_url, "t": token}, separators=(",", ":"))
    return _to_urlsafe_b64(payload.encode("utf-8"))


def decode_activation_code(code: str) -> tuple[str, str]:
    """encode_activation_code 的反向操作；返回 ``(base_url, token)``，格式错误抛 ValueError。"""
    raw = _b64decode(code)
    data = json.loads(raw)
    base_url = data.get("b")
    token = data.get("t")
    if not base_url or not token:
        raise ValueError("activation code missing required fields")
    return base_url, token


def create_access_token(
    *,
    user_id: int,
    username: str,
    jti: str | None = None,
    client_context: dict | None = None,
    expires_in_seconds: int | None = None,
    purpose: str | None = None,
    login_record_id: int | None = None,
) -> tuple[str, int, str]:
    token_jti = jti or uuid4().hex
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


async def create_admin_token(client_version: str = "", ip_address: str = "", user_agent: str = "") -> tuple[str, int]:
    jti = uuid4().hex
    expires_delta = timedelta(minutes=SETTINGS.access_token_expire_minutes)
    expires_at = datetime.now(UTC) + expires_delta
    payload = {"sub": "admin", "username": SETTINGS.admin_username, "is_admin": True, "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, SETTINGS.jwt_secret_key, algorithm=SETTINGS.jwt_algorithm)
    # deps.get_current_admin_token 要求 jti 行已存在，insert 失败必须抛出来而非 mint 一个首调就 401 的 token。
    async with SESSION_LOCAL() as db:
        db.add(
            AdminSession(
                token_jti=jti,
                username=SETTINGS.admin_username,
                client_version=client_version[:64],
                ip_address=ip_address[:64],
                user_agent=user_agent[:1024],
                is_active=True,
            ),
        )
        await db.commit()
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, SETTINGS.jwt_secret_key, algorithms=[SETTINGS.jwt_algorithm])


def decode_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌。")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效。") from exc
