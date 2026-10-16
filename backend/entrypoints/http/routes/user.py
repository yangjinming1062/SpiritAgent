from fastapi import Request
from features.accounts.schemas import ActivateRequest, RefreshRequest, TokenResponse
from features.accounts.session_operations import activate_account, logout_login, refresh_login
from infrastructure.config import SETTINGS
from infrastructure.database import DbSession
from infrastructure.http_contracts import operation_policy
from runtime.channels import terminate_user_gateway
from runtime.responses import MessageResponse
from slowapi.util import get_remote_address

from entrypoints.http.auth import CurrentSession
from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router()


@router.post(
    "/activate",
    response_model=TokenResponse,
    openapi_extra=operation_policy(ipc=False),
)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def activate(payload: ActivateRequest, request: Request, db: DbSession) -> TokenResponse:
    """用激活码换取会话 JWT：激活码是 base64url JSON {b, t}，t 字段经哈希后按 activation_token_hash 查用户；成功后停用旧会话、签发 JWT 并写 LoginRecord。"""
    result = await activate_account(
        payload,
        db,
        ip_address=getattr(request.client, "host", "") or "",
        user_agent=request.headers.get("user-agent", ""),
    )
    await terminate_user_gateway(result.user.id)
    return result


@router.post(
    "/refresh",
    response_model=TokenResponse,
    openapi_extra=operation_policy(ipc=False),
)
async def refresh_session(
    payload: RefreshRequest,
    request: Request,
    session: CurrentSession,
    db: DbSession,
) -> TokenResponse:
    user, login_record = session
    return await refresh_login(
        user.id,
        login_record.id,
        payload,
        db,
        ip_address=getattr(request.client, "host", "") or "",
        user_agent=request.headers.get("user-agent", ""),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    openapi_extra=operation_policy(ipc=False),
)
async def logout(session: CurrentSession, db: DbSession) -> MessageResponse:
    user, login_record = session
    await logout_login(user.id, login_record.id, db)
    await terminate_user_gateway(user.id, login_record_id=login_record.id)
    return MessageResponse(message="已退出登录。")
