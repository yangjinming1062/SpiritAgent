from fastapi import HTTPException, Request
from features.accounts.schemas import ActivateRequest, RefreshRequest, TokenResponse, UserInfo
from features.accounts.security import create_ws_ticket
from features.accounts.session_operations import activate_account, logout_login, refresh_login
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.request_errors import RequestError
from runtime.channels import terminate_user_gateway
from runtime.responses import MessageResponse
from slowapi.util import get_remote_address

from entrypoints.http.auth import CurrentSession
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router()


@router.post("/activate", response_model=TokenResponse)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def activate(payload: ActivateRequest, request: Request, db: DbSession) -> TokenResponse:
    """用激活码换取会话 JWT：激活码是 base64url JSON {b, t}，t 字段经哈希后按 activation_token_hash 查用户；成功后停用旧会话、签发 JWT 并写 LoginRecord。"""
    try:
        result = await activate_account(
            payload,
            db,
            ip_address=getattr(request.client, "host", "") or "",
            user_agent=request.headers.get("user-agent", ""),
        )
        await terminate_user_gateway(result.user.id)
        return result
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/ws-ticket", response_model=TokenResponse)
async def mint_ws_ticket(session: CurrentSession) -> TokenResponse:
    """签发仅供 WS 的短期 JWT，避免 renderer 持有长寿命 bearer。"""
    user, login_record = session
    token, expires_in = create_ws_ticket(
        user_id=user.id,
        username=user.username,
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
    try:
        user, login_record = session
        return await refresh_login(
            user.id,
            login_record.id,
            payload,
            db,
            ip_address=getattr(request.client, "host", "") or "",
            user_agent=request.headers.get("user-agent", ""),
        )
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/logout", response_model=MessageResponse)
async def logout(session: CurrentSession, db: DbSession) -> MessageResponse:
    try:
        user, login_record = session
        await logout_login(user.id, login_record.id, db)
        await terminate_user_gateway(user.id, login_record_id=login_record.id)
        return MessageResponse(message="已退出登录。")
    except RequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
