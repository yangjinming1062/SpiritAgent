import hmac
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse
from features.accounts.schemas import AdminLoginRequest, AdminTokenResponse
from features.accounts.security import create_admin_token
from infrastructure.config import SETTINGS
from slowapi.util import get_remote_address

from entrypoints.http.rate_limit import limiter
from entrypoints.http.routing import get_router

router = get_router(prefix="", tag="admin", identity="public")

ADMIN_STATIC_PATH = Path(__file__).resolve().parents[3] / "static"


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


@router.get("/admin/", include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_STATIC_PATH / "admin.html", headers={"Cache-Control": "no-cache"})


@router.get("/admin/admin.js", include_in_schema=False)
async def admin_script() -> FileResponse:
    return FileResponse(
        ADMIN_STATIC_PATH / "admin.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/admin/admin.css", include_in_schema=False)
async def admin_styles() -> FileResponse:
    return FileResponse(
        ADMIN_STATIC_PATH / "admin.css",
        media_type="text/css",
        headers={"Cache-Control": "no-cache"},
    )
