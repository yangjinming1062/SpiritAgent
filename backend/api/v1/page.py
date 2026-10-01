import hmac
from pathlib import Path

from common import get_router
from components import SETTINGS
from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse
from modules.auth import AdminLoginRequest, AdminTokenResponse, create_admin_token
from services.adapters.http import limiter
from slowapi.util import get_remote_address

router = get_router(prefix="", tag="admin")

ADMIN_HTML_PATH = Path(__file__).parent.parent.parent / "static" / "admin.html"


@router.post("/admin/login", response_model=AdminTokenResponse)
@limiter.limit(lambda: f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def admin_login(payload: AdminLoginRequest, request: Request) -> AdminTokenResponse:
    # compare_digest 仅接受 ASCII str，先编码避免非 ASCII 输入触发 TypeError（500）而非 401
    user_ok = hmac.compare_digest(payload.username.encode("utf-8"), SETTINGS.admin_username.encode("utf-8"))
    pass_ok = hmac.compare_digest(payload.password.encode("utf-8"), SETTINGS.admin_password.encode("utf-8"))
    if not user_ok or not pass_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    token, expires_in = await create_admin_token()
    return AdminTokenResponse(access_token=token, expires_in=expires_in)


@router.get("/admin/")
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_HTML_PATH)
