import re
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .logger import current_request_id, get_logger, set_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_INBOUND_ID_LEN = 64
# 拒绝控制字符 / 空格 / 非 ASCII，防 CRLF 头注入与日志 flood；字符集 [A-Za-z0-9._-]+ 同时覆盖 ULID / W3C trace-id / uuid hex / dotted form。
_VALID_INBOUND_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")

logger = get_logger(__name__)


def normalize_inbound(value: str | None) -> str | None:
    """校验客户端 X-Request-ID；不合法返 None 让 caller 自行 mint。"""
    if value is None:
        return None
    if not value or len(value) > _MAX_INBOUND_ID_LEN:
        return None
    if not _VALID_INBOUND_CHARS.match(value):
        return None
    return value


def new_request_id() -> str:
    """项目通用 hex ID 生成器（call_id / jti / 工具 request_id / 文件名种子都走这里）；utils.py 因属于基础设施层（与 config/logger 平级）可例外。"""
    return uuid.uuid4().hex


def adopt_inbound(header_value: str | None) -> str:
    """单一 verb 覆盖 HTTP middleware 与 WS upgrade 入口：normalize → mint → 绑 ContextVar。"""
    rid = normalize_inbound(header_value) or new_request_id()
    set_request_id(rid)
    return rid


def begin_local_scope() -> str:
    """长生命周期 task tick 入口 mint 新 ID，使该 tick 所有日志共享同一 request_id。"""
    rid = new_request_id()
    set_request_id(rid)
    return rid


async def correlated_exception_response(request: Request, exc: Exception) -> JSONResponse:
    """500 兜底 handler：把 ContextVar 里的 request_id 写进 response header（ServerErrorMiddleware 在最外层，user middleware 的 post-call_next 不会跑）。"""
    rid = current_request_id()
    logger.exception(
        "Unhandled server exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=exc,
        extra={"request_id": rid},
    )
    headers = {"X-Request-ID": rid} if rid else {}
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "reason": "internal_error", "status": 500},
        headers=headers,
    )


async def correlation_id_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    """覆盖所有 path（非仅 /api/*）；不 reset ContextVar（Starlette 每请求独立 task 自动隔离）；500 header 透传交给 main.py 的 ExceptionMiddleware 兜底。"""
    inbound = request.headers.get(REQUEST_ID_HEADER)
    rid = adopt_inbound(inbound)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = rid
    return response
