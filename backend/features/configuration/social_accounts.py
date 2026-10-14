"""管理端的运营社交账户登录能力，只使用已保存服务端配置。"""

import asyncio

from infrastructure.config import SETTINGS
from infrastructure.request_errors import RequestError
from infrastructure.web.social_content import SocialSourceError
from infrastructure.web.xiaohongshu import XhsLoginQrCode, XhsLoginStatus, XiaohongshuServiceClient
from runtime.responses import MessageResponse

_login_mutation_lock = asyncio.Lock()


class XiaohongshuAdminLoginStatus(XhsLoginStatus):
    configured: bool


def _client() -> XiaohongshuServiceClient:
    if not SETTINGS.xhs_mcp_base_url:
        raise RequestError(status_code=409, detail="请先保存小红书自托管服务地址。")
    try:
        return XiaohongshuServiceClient(base_url=SETTINGS.xhs_mcp_base_url, auth_token=SETTINGS.xhs_mcp_auth_token)
    except ValueError:
        raise RequestError(status_code=409, detail="小红书自托管服务配置无效，请检查已保存配置。") from None


def _request_failure(error: SocialSourceError) -> RequestError:
    detail = (
        "无法连接小红书自托管服务，请检查服务地址与运行状态。"
        if error.reason == "network_error"
        else (
            "小红书服务鉴权失败，请检查已保存的服务令牌。"
            if error.code in {401, 403}
            else "小红书自托管服务未返回有效的登录结果，请检查服务状态。"
        )
    )
    return RequestError(status_code=502, detail=detail)


async def login_status() -> XiaohongshuAdminLoginStatus:
    if not SETTINGS.xhs_mcp_base_url:
        return XiaohongshuAdminLoginStatus(configured=False, is_logged_in=False)
    try:
        result = await _client().status()
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
            await _client().clear_saved_login()
        except SocialSourceError as exc:
            raise _request_failure(exc) from None
    return MessageResponse(message="已清除小红书已保存的登录信息；现存二维码有效期内仍可能完成登录。")
