from collections.abc import Awaitable, Callable

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from features.accounts.security import decode_access_token
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger, set_request_user_id
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from entrypoints.http.errors import error_response

logger = get_logger(__name__)


def _user_key(request: Request) -> str:
    """鉴权依赖提供账户标识；未认证请求按 IP 限流。"""
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
limiter = DynamicLimiter(key_func=_user_key, storage_uri="memory://", config_filename="")


def rate_limit_bearer_claims(request: Request) -> dict[str, object] | None:
    """限流共用的请求级验签结果。无 Bearer 返回 None，无效 Bearer 返回空映射。"""
    if hasattr(request.state, "rate_limit_claims"):
        return request.state.rate_limit_claims
    scheme, token = get_authorization_scheme_param(request.headers.get("authorization"))
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError as exc:
        logger.debug("rate_limit: JWT decode failed: %s", exc)
        claims = {}
    request.state.rate_limit_claims = claims
    return claims


async def stash_user_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """提前标记 Bearer 身份；Cookie 身份由端点鉴权依赖提供。"""
    if request.url.path.startswith("/api/") and (claims := rate_limit_bearer_claims(request)):
        sub = claims.get("sub")
        if isinstance(sub, str) and sub.isascii() and sub.isdecimal():
            uid = int(sub)
            request.state.user_id = uid
            set_request_user_id(uid)
    return await call_next(request)


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """统一错误信封，Retry-After 使用当前限流窗口。"""
    retry_after = int(exc.limit.limit.get_expiry())
    response = error_response(429, "Rate limit exceeded")
    response.headers["Retry-After"] = str(retry_after)
    return response
