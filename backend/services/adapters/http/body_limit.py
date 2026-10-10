"""在框架读取 JSON / multipart 前限制请求体；大上传入口先核对有效登录。"""

import re

from components import SESSION_LOCAL, SETTINGS, STT_MAX_AUDIO_BYTES
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from modules.auth import authenticate_remote_session, get_current_admin_token, get_current_session
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_KIB = 1024
_MIB = 1024 * _KIB
_DEFAULT_MAX_BYTES = 64 * _KIB
_UPLOAD_OVERHEAD_BYTES = _MIB
_IMAGE_UPLOAD_PATH = re.compile(
    r"/api/companion/(?:avatar/(?:from-image|adopt|\d+/fullbody/reference(?:/adopt)?)"
    r"|outfits(?:/(?:prompt|adopt|\d+/adopt))?"
    r"|scenes/(?:generate|adopt|\d+/adopt))",
)
_BACKUP_UPLOAD_PATH = re.compile(r"/api/admin/users/\d+/import")
_DESKTOP_VIDEO_UPLOAD_PATH = re.compile(r"/api/companion/desktop-videos/actions/\d+/upload")


class _RequestBodyTooLarge(MultiPartException):
    """沿 multipart 的异常路径关闭已 spool 的文件，同时由外层转换成 413。"""


def _body_policy(scope: Scope) -> tuple[int, bool | None]:
    path = scope["path"].rstrip("/")
    if scope["method"] == "PUT" and path == "/api/companion/persona":
        return 32 * _KIB, None
    if scope["method"] != "POST":
        return _DEFAULT_MAX_BYTES, None
    if _IMAGE_UPLOAD_PATH.fullmatch(path):
        # 最大请求含两个 8 MiB base64 图像字段；其余字段继续由 DTO 校验。
        return 16 * _MIB + _DEFAULT_MAX_BYTES, False
    if path == "/api/companion/video-packs":
        # 12 个 32 MiB 片段经 base64 编码后的总量，加上 JSON 字段开销。
        return 512 * _MIB + _DEFAULT_MAX_BYTES, False
    if path == "/api/llm/completion":
        # Client / Runner 的视觉补全载荷预算为 10 MiB。
        return 10 * _MIB + _DEFAULT_MAX_BYTES, False
    if path == "/api/media/videos":
        return SETTINGS.video_attachment_max_bytes + _UPLOAD_OVERHEAD_BYTES, False
    if _DESKTOP_VIDEO_UPLOAD_PATH.fullmatch(path):
        return SETTINGS.video_attachment_max_bytes + _UPLOAD_OVERHEAD_BYTES, False
    if path == "/api/media/stt":
        return STT_MAX_AUDIO_BYTES + _UPLOAD_OVERHEAD_BYTES, False
    if _BACKUP_UPLOAD_PATH.fullmatch(path):
        return 4 * 1024 * _MIB + _UPLOAD_OVERHEAD_BYTES, True
    if path == "/api/update/versions":
        return 1024 * _MIB + _UPLOAD_OVERHEAD_BYTES, True
    return _DEFAULT_MAX_BYTES, None


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
        elif credentials is not None:
            await get_current_session(credentials, db)
        else:
            await authenticate_remote_session(request, db, content_only=True)


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
            await JSONResponse(status_code=400, content={"detail": "无效的 Content-Length。"})(scope, receive, send)
            return
        too_large = JSONResponse(
            status_code=413,
            content={"detail": "请求内容超过大小上限。"},
        )
        # 先按长度判断位数，避免不可信的超长整数头触发 int() 的解释器限制。
        if lengths and (len(lengths[0]) > 20 or int(lengths[0]) > max_bytes):
            await too_large(scope, receive, send)
            return
        if admin_only is not None:
            try:
                await _authenticate_upload(scope, admin_only=admin_only)
            except HTTPException as exc:
                await JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)(
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
