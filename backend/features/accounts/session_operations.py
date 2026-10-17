"""桌面登录与会话凭据的事务入口。"""

from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.text import utc_now
from runtime.user_maintenance import is_user_in_maintenance
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.models import LoginRecord, User, lock_user_row
from features.accounts.schemas import ActivateRequest, RefreshRequest, TokenResponse, UserInfo
from features.accounts.security import create_access_token, decode_activation_code, hash_activation_token


async def activate_account(
    payload: ActivateRequest,
    db: AsyncSession,
    *,
    ip_address: str,
    user_agent: str,
) -> TokenResponse:
    try:
        _base_url, raw_token = decode_activation_code(payload.code)
    except Exception:
        raise RequestError(status_code=401, message="激活码格式无效。") from None
    token_hash = hash_activation_token(raw_token)
    user = await db.scalar(
        select(User).where(User.activation_token_hash == token_hash, User.is_active.is_(True)).with_for_update(),
    )
    if user is None:
        raise RequestError(status_code=401, message="激活码无效。")
    if is_user_in_maintenance(user.id):
        raise RequestError(status_code=503, message="用户数据正在维护，请稍后重试。")
    now = utc_now()
    await db.execute(
        update(LoginRecord)
        .where(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True))
        .values(is_active=False, logout_at=now),
    )
    context = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(user_id=user.id, username=user.username, client_context=context)
    db.add(
        LoginRecord(
            user_id=user.id,
            token_jti=token_jti,
            client_version=payload.client_version,
            ip_address=ip_address,
            user_agent=user_agent,
            is_active=True,
            login_at=now,
        ),
    )
    await db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


async def refresh_login(
    user_id: int,
    login_record_id: int,
    payload: RefreshRequest,
    db: AsyncSession,
    *,
    ip_address: str,
    user_agent: str,
) -> TokenResponse:
    await lock_user_row(db, user_id)
    if is_user_in_maintenance(user_id):
        raise RequestError(status_code=503, message="用户数据正在维护，请稍后重试。")
    row = (
        await db.execute(
            select(User, LoginRecord)
            .join(LoginRecord, LoginRecord.user_id == User.id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                LoginRecord.id == login_record_id,
                LoginRecord.is_active.is_(True),
            ),
        )
    ).one_or_none()
    if row is None:
        raise RequestError(status_code=401, message="当前会话已失效，请重新登录")
    user, login = row
    context = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(user_id=user.id, username=user.username, client_context=context)
    login.token_jti = token_jti
    login.client_version = payload.client_version
    login.ip_address = ip_address
    login.user_agent = user_agent
    db.add(login)
    await db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


async def logout_login(user_id: int, login_record_id: int, db: AsyncSession) -> None:
    await lock_user_row(db, user_id)
    login = await db.scalar(
        select(LoginRecord).where(LoginRecord.id == login_record_id, LoginRecord.user_id == user_id),
    )
    if login is None:
        raise RequestError(status_code=401, message="当前会话已失效，请重新登录")
    login.is_active = False
    login.logout_at = utc_now()
    db.add(login)
    await db.commit()


async def is_login_active(user_id: int, login_record_id: int) -> bool:
    async with SESSION_LOCAL() as db:
        return (
            await db.scalar(
                select(LoginRecord.id)
                .join(User, User.id == LoginRecord.user_id)
                .where(
                    User.id == user_id,
                    User.is_active.is_(True),
                    LoginRecord.id == login_record_id,
                    LoginRecord.is_active.is_(True),
                ),
            )
            is not None
        )
