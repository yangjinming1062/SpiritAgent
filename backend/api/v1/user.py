from common import get_router
from components import SETTINGS, DbSession, utc_now
from fastapi import HTTPException, Request, status
from modules.auth import (
    ActivateRequest,
    CurrentSession,
    LoginRecord,
    RefreshRequest,
    TokenResponse,
    User,
    UserInfo,
    create_access_token,
    decode_activation_code,
    hash_activation_token,
)
from modules.system import MessageResponse
from services.adapters.desktop.handlers import terminate_user_gateway
from services.adapters.http.rate_limit import limiter
from slowapi.util import get_remote_address
from sqlalchemy import select

WS_TICKET_TTL_SECONDS = 60


router = get_router()

# 短期 ticket TTL：足以开 WS、重放前已过期。


@router.post("/activate", response_model=TokenResponse)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def activate(payload: ActivateRequest, request: Request, db: DbSession) -> TokenResponse:
    """用激活码换取会话 JWT：激活码是 base64url JSON {b, t}，t 字段经哈希后按 activation_token_hash 查用户；成功后流程同旧登录（停用旧会话、签发 JWT、写 LoginRecord）。"""
    try:
        _base_url, raw_token = decode_activation_code(payload.code)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="激活码格式无效。")

    token_hash = hash_activation_token(raw_token)
    user = (
        await db.execute(select(User).where(User.activation_token_hash == token_hash, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="激活码无效。")

    now = utc_now()
    for record in (
        (await db.execute(select(LoginRecord).where(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True))))
        .scalars()
        .all()
    ):
        record.is_active = False
        record.logout_at = now
        db.add(record)

    client_ctx_dict = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(
        user_id=user.id,
        username=user.username,
        client_context=client_ctx_dict,
    )
    db.add(
        LoginRecord(
            user_id=user.id,
            token_jti=token_jti,
            client_version=payload.client_version,
            ip_address=getattr(request.client, "host", "") or "",
            user_agent=request.headers.get("user-agent", ""),
            is_active=True,
            login_at=now,
            last_seen_at=now,
        ),
    )
    await db.commit()
    await terminate_user_gateway(user.id)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/ws-ticket", response_model=TokenResponse)
async def mint_ws_ticket(session: CurrentSession) -> TokenResponse:
    """签发仅供 WS 的短期 JWT，避免 renderer 持有长寿命 bearer。"""
    user, login_record = session
    token, expires_in, _ = create_access_token(
        user_id=user.id,
        username=user.username,
        expires_in_seconds=WS_TICKET_TTL_SECONDS,
        purpose="ws",
        login_record_id=login_record.id,
    )
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    payload: RefreshRequest,
    request: Request,
    session: CurrentSession,
    db: DbSession,
) -> TokenResponse:
    now = utc_now()
    user, login_record = session

    client_ctx_dict = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(
        user_id=user.id,
        username=user.username,
        client_context=client_ctx_dict,
    )
    login_record.token_jti = token_jti
    login_record.client_version = payload.client_version
    login_record.ip_address = getattr(request.client, "host", "") or ""
    login_record.user_agent = request.headers.get("user-agent", "")
    login_record.last_seen_at = now
    db.add(login_record)
    await db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
async def logout(session: CurrentSession, db: DbSession) -> MessageResponse:
    user, login_record = session
    login_record.is_active = False
    login_record.logout_at = utc_now()
    db.add(login_record)
    await db.commit()
    await terminate_user_gateway(user.id, login_record_id=login_record.id)
    return MessageResponse(message="已退出登录。")
