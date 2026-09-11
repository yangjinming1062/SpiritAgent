import hmac
from pathlib import Path

from common import get_router
from components import SETTINGS
from fastapi import HTTPException, status
from fastapi.responses import FileResponse
from modules.auth import AdminLoginRequest, AdminTokenResponse, create_admin_token

router = get_router(prefix="", tag="admin")

ADMIN_HTML_PATH = Path(__file__).parent.parent.parent / "static" / "admin.html"


@router.post("/admin/login", response_model=AdminTokenResponse)
async def admin_login(payload: AdminLoginRequest) -> AdminTokenResponse:
    if not hmac.compare_digest(payload.username, SETTINGS.admin_username) or not hmac.compare_digest(
        payload.password,
        SETTINGS.admin_password,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    token, expires_in = await create_admin_token()
    return AdminTokenResponse(access_token=token, expires_in=expires_in)


@router.get("/admin/")
async def admin_page() -> FileResponse:
    return FileResponse(ADMIN_HTML_PATH)
