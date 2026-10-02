from collections.abc import Awaitable, Callable

import jwt
from components import SETTINGS, get_logger, set_request_user_id
from fastapi import Request
from fastapi.responses import JSONResponse
from modules.auth import decode_access_token
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

logger = get_logger(__name__)


def _user_key(request: Request) -> str:
    """主键按 user_id，无 JWT 时降级按 IP 兜底，避免未认证请求绕过每用户配额。"""
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


class DynamicLimiter(Limiter):
    """开关每次直读 SETTINGS，热更新无需重建限流器；基类构造时的赋值被忽略。"""

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.rate_limit_enabled)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        pass


# 内存存储（单进程，见 ARCHITECTURE「部署与运行时边界」）；config_filename="" 关闭 slowapi 自动读 .env（其默认 open 在 Windows cp936 遇到 UTF-8 BOM 会崩，配项由 pydantic-settings 加载）。
limiter = DynamicLimiter(
    key_func=_user_key,
    storage_uri="memory://",
    config_filename="",
)


async def stash_user_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """尽力解析 Bearer JWT 并把 user_id 暂存到 request.state，供限流 key 使用；仅校验签名不做 DB 校验，鉴权失败由 handler 的 Depends 兜底。"""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    auth = request.headers.get("authorization")
    if auth is None or auth[:7].lower() != "bearer ":
        return await call_next(request)
    token = auth[7:].strip()
    if not token:
        return await call_next(request)
    try:
        # 用户 token 的 sub 是数字字符串；admin token 的 sub 是 "admin"，不参与按用户限流。
        sub = decode_access_token(token).get("sub")
        if isinstance(sub, str) and sub.isdigit():
            uid = int(sub)
            request.state.user_id = uid
            # 同步到 logger 的 ContextVar, 后续本 task log 自动带 user_id 字段
            set_request_user_id(uid)
    except jwt.PyJWTError as exc:
        logger.debug("rate_limit: JWT decode failed in stash middleware: %s", exc)
    return await call_next(request)


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 响应：标准 {error, reason, status} 信封 + Retry-After（从限流项实际窗口推导，与上游 429 路径统一 reason 枚举）。"""
    retry_after = int(exc.limit.limit.get_expiry())
    response = JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded", "reason": "rate_limit", "status": 429},
    )
    response.headers["Retry-After"] = str(retry_after)
    return response
