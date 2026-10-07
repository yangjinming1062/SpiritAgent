import hmac
from collections.abc import AsyncIterator

from components import (
    SESSION_LOCAL,
    begin_user_request,
    end_user_request,
    ensure_utc,
    get_db,
    is_user_in_maintenance,
    set_request_user_id,
    utc_now,
)
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.remote import (
    REMOTE_COOKIE,
    RemoteSession,
    hash_remote_token,
    remote_content_allowed,
    remote_csrf_token,
    require_remote_origin,
)

from .models import AdminSession, LoginRecord, User
from .security import BEARER_SCHEME, decode_bearer_token


async def get_current_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: AsyncSession = Depends(get_db),
) -> str:
    """校验 admin token 的 jti 在 admin_sessions 中 is_active=True，使强制吊销立即生效而非等 JWT 自然过期。"""
    payload = decode_bearer_token(credentials)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="非管理员令牌。")
    username = payload.get("username")
    jti = payload.get("jti")
    if not username or not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    session = (
        await db.execute(select(AdminSession).where(AdminSession.token_jti == jti, AdminSession.is_active.is_(True)))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员令牌已吊销或未登记，请重新登录。")
    # 读完即提交，理由同 get_current_session。
    await db.commit()
    return username


async def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, LoginRecord]:
    payload = decode_bearer_token(credentials)

    if payload.get("purpose") is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌用途无效。")

    user_id = payload.get("sub")
    token_jti = payload.get("jti")
    if not user_id or not token_jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌缺少必要字段。")

    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌用户标识无效。")

    login_record = (
        await db.execute(select(LoginRecord).where(LoginRecord.token_jti == token_jti, LoginRecord.is_active.is_(True)))
    ).scalar_one_or_none()
    if login_record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前会话已失效，请重新登录")

    user = (await db.execute(select(User).where(User.id == uid, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用。")
    if is_user_in_maintenance(uid):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="用户数据正在维护，请稍后重试。")
    # get_db 是请求级依赖，响应发送完毕才关闭会话；鉴权读取不能让事务跨过模型等待、文件与流式下发。提交（而非回滚）后 user 与 login_record 不过期，仍可使用。
    await db.commit()
    return user, login_record


async def get_optional_current_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, LoginRecord | RemoteSession] | None:
    if credentials is None and not request.cookies.get(REMOTE_COOKIE):
        return None
    try:
        if credentials is None or not credentials.credentials:
            return await authenticate_remote_session(request, db, content_only=True)
        return await get_current_session(credentials, db)
    except HTTPException:
        # 鉴权失败被吞掉后路由仍继续（如按签名下发文件），读事务不能留到响应结束。
        await db.rollback()
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: AsyncSession = Depends(get_db),
) -> AsyncIterator[User]:
    session = (
        await get_current_session(credentials, db)
        if credentials is not None
        else await authenticate_remote_session(request, db, content_only=True)
    )
    user = session[0]
    if not await begin_user_request(user.id):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="用户数据正在维护，请稍后重试。")
    try:
        yield user
    finally:
        await end_user_request(user.id)


async def authenticate_remote_session(
    request: Request,
    db: AsyncSession,
    *,
    content_only: bool = False,
) -> tuple[User, RemoteSession]:
    if content_only and not remote_content_allowed(request.url.path, request.method):
        raise HTTPException(status_code=403, detail="手机授权不能访问此功能。")
    token = request.cookies.get(REMOTE_COOKIE)
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="请重新扫码连接。")
    row = (
        await db.execute(
            select(User, RemoteSession)
            .join(RemoteSession, RemoteSession.user_id == User.id)
            .where(
                RemoteSession.token_hash == hash_remote_token(token),
                RemoteSession.revoked_at.is_(None),
                RemoteSession.expires_at > utc_now(),
                User.is_active.is_(True),
            ),
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="手机授权已失效，请重新扫码连接。")
    user, device = row
    if is_user_in_maintenance(user.id):
        raise HTTPException(status_code=503, detail="用户数据正在维护，请稍后重试。")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_remote_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if len(csrf) != 64 or not csrf.isascii() or not hmac.compare_digest(csrf, remote_csrf_token(token)):
            raise HTTPException(status_code=403, detail="页面授权校验失败，请刷新页面。")
    request.state.user_id = user.id
    request.state.remote_device_id = device.id
    set_request_user_id(user.id)
    if (utc_now() - ensure_utc(device.last_seen_at)).total_seconds() >= 60:
        device.last_seen_at = utc_now()
    await db.commit()
    return user, device


async def get_current_remote_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AsyncIterator[tuple[User, RemoteSession]]:
    session = await authenticate_remote_session(request, db)
    if not await begin_user_request(session[0].id):
        raise HTTPException(status_code=503, detail="用户数据正在维护，请稍后重试。")
    try:
        yield session
    finally:
        await end_user_request(session[0].id)


async def is_remote_session_active(user_id: int, device_id: int) -> bool:
    if is_user_in_maintenance(user_id):
        return False
    async with SESSION_LOCAL() as db:
        device = await db.scalar(
            select(RemoteSession)
            .join(User, User.id == RemoteSession.user_id)
            .where(
                RemoteSession.id == device_id,
                RemoteSession.user_id == user_id,
                RemoteSession.revoked_at.is_(None),
                RemoteSession.expires_at > utc_now(),
                User.is_active.is_(True),
            ),
        )
        if device is None:
            return False
        if (utc_now() - ensure_utc(device.last_seen_at)).total_seconds() >= 60:
            device.last_seen_at = utc_now()
            await db.commit()
        return True
