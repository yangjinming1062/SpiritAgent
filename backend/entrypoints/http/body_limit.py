"""在框架读取 JSON / multipart 前限制请求体；大上传入口先核对有效登录。"""

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from features.accounts.deps import authenticate_request_session, get_current_admin_token
from infrastructure.config import SETTINGS
from infrastructure.constants import ATTACHMENT_DATA_URL_MAX_CHARS, SCENE_DOWNLOAD_MAX_BYTES, STT_MAX_AUDIO_BYTES
from infrastructure.database import SESSION_LOCAL
from infrastructure.http_contracts import find_http_operation
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from entrypoints.http.errors import error_response

_KIB = 1024
_MIB = 1024 * _KIB
_DEFAULT_MAX_BYTES = 64 * _KIB
_UPLOAD_OVERHEAD_BYTES = _MIB


class _RequestBodyTooLarge(MultiPartException):
    """沿 multipart 的异常路径关闭已 spool 的文件，同时由外层转换成 413。"""


def _body_policy(scope: Scope) -> tuple[int, bool | None]:
    operation = find_http_operation(scope["path"], scope["method"])
    if operation is None or operation.upload == "json":
        return _DEFAULT_MAX_BYTES, None
    limits = {
        "chat": SETTINGS.max_attachments_per_turn * ATTACHMENT_DATA_URL_MAX_CHARS + _MIB,
        "image": 16 * _MIB + _DEFAULT_MAX_BYTES,
        "image_file": SCENE_DOWNLOAD_MAX_BYTES + _UPLOAD_OVERHEAD_BYTES,
        "action_pack": 512 * _MIB + _DEFAULT_MAX_BYTES,
        "video": SETTINGS.video_attachment_max_bytes + _UPLOAD_OVERHEAD_BYTES,
        "audio": STT_MAX_AUDIO_BYTES + _UPLOAD_OVERHEAD_BYTES,
        "backup": 4 * 1024 * _MIB + _UPLOAD_OVERHEAD_BYTES,
        "release": 1024 * _MIB + _UPLOAD_OVERHEAD_BYTES,
    }
    return limits[operation.upload], operation.permits("admin")


async def _authenticate_upload(scope: Scope, *, admin_only: bool) -> None:
    request = Request(scope)
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    credentials = (
        HTTPAuthorizationCredentials(scheme=scheme, credentials=token.strip())
        if scheme.lower() == "bearer" and token.strip()
        else None
    )
    async with SESSION_LOCAL() as db:
        if admin_only:
            await get_current_admin_token(credentials, db)
        else:
            await authenticate_request_session(request, credentials, db)


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes, admin_only = _body_policy(scope)
        headers = Headers(scope=scope)
        lengths = headers.getlist("content-length")
        if lengths and (len(set(lengths)) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()):
            await error_response(400, "无效的 Content-Length。")(scope, receive, send)
            return
        too_large = error_response(413, "请求内容超过大小上限。")
        # 先按长度判断位数，避免不可信的超长整数头触发 int() 的解释器限制。
        if lengths and (len(lengths[0]) > 20 or int(lengths[0]) > max_bytes):
            await too_large(scope, receive, send)
            return
        if admin_only is not None:
            try:
                await _authenticate_upload(scope, admin_only=admin_only)
            except HTTPException as exc:
                await error_response(exc.status_code, str(exc.detail), headers=exc.headers)(
                    scope,
                    receive,
                    send,
                )
                return

        received_bytes = 0
        exceeded = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, exceeded
            if exceeded:
                raise _RequestBodyTooLarge("请求内容超过大小上限。")
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_bytes:
                    exceeded = True
                    raise _RequestBodyTooLarge("请求内容超过大小上限。")
            return message

        async def limited_send(message: Message) -> None:
            nonlocal rejected
            if exceeded:
                if not rejected:
                    rejected = True
                    await too_large(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except _RequestBodyTooLarge:
            if not rejected:
                await too_large(scope, receive, send)
