from collections.abc import AsyncIterator

from components import LOGIN_HEARTBEAT_INTERVAL_SECONDS, ensure_utc, get_db, utc_now
from components.user_maintenance_runtime import begin_user_request, end_user_request, is_user_in_maintenance
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效。")
    session = (
        await db.execute(select(AdminSession).where(AdminSession.token_jti == jti, AdminSession.is_active.is_(True)))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员令牌已吊销或未登记，请重新登录。")
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

    now = utc_now()
    if (
        login_record.last_seen_at is None
        or (now - ensure_utc(login_record.last_seen_at)).total_seconds() > LOGIN_HEARTBEAT_INTERVAL_SECONDS
    ):
        login_record.last_seen_at = now
        db.add(login_record)
        await db.commit()
    return user, login_record


async def get_optional_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER_SCHEME),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, LoginRecord] | None:
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_session(credentials, db)
    except HTTPException:
        return None


async def get_current_user(
    session: tuple[User, LoginRecord] = Depends(get_current_session),
) -> AsyncIterator[User]:
    user = session[0]
    if not await begin_user_request(user.id):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="用户数据正在维护，请稍后重试。")
    try:
        yield user
    finally:
        await end_user_request(user.id)


async def get_current_login_record(
    session: tuple[User, LoginRecord] = Depends(get_current_session),
) -> LoginRecord:
    return session[1]
