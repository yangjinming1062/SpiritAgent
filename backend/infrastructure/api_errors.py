"""传输边界共用的脱敏业务错误。"""

import re

from pydantic import BaseModel, JsonValue

from infrastructure.logger import current_request_id
from infrastructure.redact import redact_sensitive_text

_SECRET_DETAIL_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "auth",
        "bearer",
        "authorization",
        "cookie",
        "set_cookie",
        "client_secret",
        "secret_value",
        "raw_secret",
        "secret_input",
        "key_material",
        "connection_string",
        "dsn",
        "activation_code",
        "ticket",
    },
)

_STATUS_CODES = {
    400: "invalid_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limit",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(BaseModel):
    code: str
    message: str
    details: JsonValue = None
    request_id: str


class ApiErrorResponse(BaseModel):
    error: ApiError


# 必须从给 renderer 的错误里抹掉的服务端内部痕迹（PROTOCOL「错误信封」）。精选而非宽泛：文件系统路径、DSN/URL 凭据、OpenAI/httpx 异常格式、Python traceback 行。
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*Traceback \(most recent call last\):.*", re.DOTALL),
    re.compile(r"(/[A-Za-z0-9_.+-]+){2,}/[A-Za-z0-9_.-]+\.py:\d+"),
    re.compile(r"[A-Za-z]:\\[A-Za-z0-9_.\\ -]+\.py:\d+"),
    re.compile(
        r"/(?:var|tmp|etc|home|root|opt|srv|mnt)/[A-Za-z0-9_./-]+",
    ),  # 通用文件系统路径（.py 模式抓不到目录/非 Python 文件）
    re.compile(
        r"postgresql(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^@\s/]+:[^@\s/]+@\S+",
    ),  # postgres DSN；延伸到 @ 后第一个空白，连内部主机名一并清掉
    re.compile(r"(?:redis|mongodb|amqp|kafka)(?:\+[A-Za-z]+)?://[^@\s/]+:[^@\s/]+@\S+"),  # 非 postgres DSN
    re.compile(
        r"(?:Bearer\s+|(?:X-)?Api-Key:\s*|(?:sk|xai|gAAAA|hsk)-)[A-Za-z0-9._\-]{12,}",
    ),  # Bearer / API-key header 值
    re.compile(
        r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abp]-|xapp-[A-Za-z0-9-]{20,})[A-Za-z0-9_-]+",
    ),  # OpenAI/GitHub/Slack 风格 token
    re.compile(
        r"\b(?:[A-Za-z0-9_]+\.)*(?:OperationalError|IntegrityError|FileNotFoundError|ConnectionError|TimeoutError)\b|\bsqlalchemy\.exc\.[A-Za-z]+\b",
    ),
    re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b",
    ),  # IPv4（含 RFC1918/loopback）——OperationalError 清理对裸 host:port 片段无效
    re.compile(r"\[?[0-9a-fA-F:]+\]?:\d{2,5}"),  # IPv6 括号包裹（[::1]:5432）与裸形式
    re.compile(r"\b(?:fe80|fc|fd)[0-9a-fA-F:]{8,}\b"),
    re.compile(
        r"\b(?:host|server)\s+(?:at\s+)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        re.IGNORECASE,
    ),  # psycopg/asyncpg 裸主机名
    re.compile(r"for user\s+\"[A-Za-z0-9_.-]+\"", re.IGNORECASE),  # PostgreSQL DSN user/role 片段
    re.compile(
        r"\b(?:password authentication failed|FATAL:\s+[A-Z][A-Za-z ]+?)\b",
        re.IGNORECASE,
    ),  # SQLAlchemy/psycopg 认证失败标签
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT 三段式
)


def redact_message(message: str) -> str:
    """错误跨传输边界前清理密钥和服务端内部痕迹。"""
    out = redact_sensitive_text(message) or ""
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[redacted]", out)
    return out[:512]


def sanitize_details(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return redact_message(value)
    if isinstance(value, (list, tuple)):
        return [sanitize_details(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower().replace("-", "_") in _SECRET_DETAIL_KEYS else sanitize_details(item)
            for key, item in value.items()
        }
    return value


def api_error(status: int, message: str, *, code: str | None = None, details: JsonValue = None) -> ApiError:
    return ApiError(
        code=code or _STATUS_CODES.get(status, f"http_{status}"),
        message=redact_message(message),
        details=sanitize_details(details),
        request_id=current_request_id() or "",
    )
