"""管理端的运营社交账户登录能力，只使用已保存服务端配置。"""

import asyncio

from infrastructure.config import SETTINGS
from infrastructure.request_errors import RequestError
from infrastructure.web.social_content import SocialSourceError
from infrastructure.web.xiaohongshu import (
    XhsLoginQrCode,
    XhsLoginStatus,
    XhsLoginVerification,
    XiaohongshuServiceClient,
)
from runtime.responses import MessageResponse

_login_mutation_lock = asyncio.Lock()


class XiaohongshuAdminLoginStatus(XhsLoginStatus):
    configured: bool


def _client() -> XiaohongshuServiceClient:
    if not SETTINGS.xhs_mcp_base_url:
        raise RequestError(status_code=409, message="请先保存小红书自托管服务地址。")
    try:
        return XiaohongshuServiceClient(base_url=SETTINGS.xhs_mcp_base_url, auth_token=SETTINGS.xhs_mcp_auth_token)
    except ValueError:
        raise RequestError(status_code=409, message="小红书自托管服务配置无效，请检查已保存配置。") from None


def _request_failure(error: SocialSourceError) -> RequestError:
    known_errors = {
        "verification_unavailable": (409, "当前小红书服务不支持短信验证，请升级为随项目构建的采集服务后重新扫码。"),
        "session_expired": (410, "小红书登录会话已到期，请重新获取二维码。"),
        "session_conflict": (409, "小红书登录会话已被替换或当前无需短信验证码，请重新检查登录状态。"),
        "login_page_check_failed": (502, "小红书登录页面状态检查失败，请重新检查登录状态；若持续失败，请刷新二维码。"),
        "login_code_input_failed": (502, "小红书验证码输入失败，请重新检查登录状态后重试。"),
        "login_confirm_button_failed": (502, "小红书验证码确认操作失败，请先检查登录状态，再决定是否重新提交。"),
        "login_cookie_save_failed": (502, "小红书登录凭据保存失败，请检查采集服务的数据卷与写入权限。"),
        "response_invalid": (502, "小红书采集服务返回的登录状态格式无法解析，请确认后端和采集服务均已更新。"),
    }
    if error.reason in known_errors:
        status_code, detail = known_errors[error.reason]
        return RequestError(status_code=status_code, message=detail)
    detail = (
        "无法连接小红书自托管服务，请检查服务地址与运行状态。"
        if error.reason == "network_error"
        else (
            "小红书服务鉴权失败，请检查已保存的服务令牌。"
            if error.code in {401, 403}
            else "小红书自托管服务未返回有效的登录结果，请检查服务状态。"
        )
    )
    return RequestError(status_code=502, message=detail)


async def login_status(*, session_id: str | None = None) -> XiaohongshuAdminLoginStatus:
    if not SETTINGS.xhs_mcp_base_url:
        return XiaohongshuAdminLoginStatus(configured=False, is_logged_in=False)
    try:
        client = _client()
        result = await client.login_session(session_id) if session_id else await client.status()
    except SocialSourceError as exc:
        raise _request_failure(exc) from None
    return XiaohongshuAdminLoginStatus(configured=True, **result.model_dump())


async def submit_login_verification(payload: XhsLoginVerification) -> XiaohongshuAdminLoginStatus:
    async with _login_mutation_lock:
        try:
            result = await _client().submit_verification(payload)
        except SocialSourceError as exc:
            raise _request_failure(exc) from None
    return XiaohongshuAdminLoginStatus(configured=True, **result.model_dump())


async def refresh_login_qrcode() -> XhsLoginQrCode:
    async with _login_mutation_lock:
        try:
            return await _client().qrcode()
        except SocialSourceError as exc:
            raise _request_failure(exc) from None


async def clear_saved_login() -> MessageResponse:
    async with _login_mutation_lock:
        try:
            cancelled = await _client().clear_saved_login()
        except SocialSourceError as exc:
            raise _request_failure(exc) from None
    return MessageResponse(
        message="已清除小红书登录信息，并结束当前扫码会话。"
        if cancelled
        else "已清除小红书已保存的登录信息；现存二维码有效期内仍可能完成登录。",
    )
