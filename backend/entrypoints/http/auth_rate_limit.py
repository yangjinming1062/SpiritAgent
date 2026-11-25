"""认证入口的来源与凭据查验限流，以及已登录会话的操作额度。"""

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from ipaddress import IPv6Address, IPv6Network, ip_address
from math import ceil
from time import monotonic

from fastapi import HTTPException, Request
from features.accounts.remote.schemas import REMOTE_TOKEN_MAX_LENGTH
from features.accounts.remote.security import hash_remote_token, remote_session_cookie, valid_remote_csrf
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from entrypoints.http.errors import http_exception_handler
from entrypoints.http.rate_limit import rate_limit_bearer_claims

_TICKET_PATH = "/api/gateway/ticket"
_SESSION_PATHS = frozenset({"/api/user/refresh", _TICKET_PATH})
AUTHENTICATION_PATHS = _SESSION_PATHS | {"/api/user/activate", "/admin/login", "/api/remote/session/exchange"}
_MINUTE_SECONDS = 60
_HOUR_SECONDS = 3600
_ATTEMPTS_PER_HOUR = 60
_FAILURES_PER_MINUTE = 5
_MAX_SOURCES_PER_PATH = 16_384
_OVERFLOW_BUCKETS = 64
_SESSION_PER_MINUTE = 30
_SESSION_PER_HOUR = 360

logger = get_logger(__name__)


@dataclass(slots=True)
class _Attempts:
    last_activity: float = float("-inf")
    requests: list[float] = field(default_factory=list)
    failures: list[float] = field(default_factory=list)
    locked_until: float = float("-inf")

    def reserve(self, now: float, per_minute: int, per_hour: int) -> None:
        del self.requests[: bisect_right(self.requests, now - _HOUR_SECONDS)]
        retry_at = self.locked_until
        if len(self.requests) >= per_minute:
            retry_at = max(retry_at, self.requests[-per_minute] + _MINUTE_SECONDS)
        if len(self.requests) >= per_hour:
            retry_at = max(retry_at, self.requests[0] + _HOUR_SECONDS)
        if retry_at > now:
            raise HTTPException(429, "请求过于频繁，请稍后再试。", headers={"Retry-After": str(ceil(retry_at - now))})
        self.requests.append(now)
        self.last_activity = now

    def record_failure(self, now: float) -> bool:
        self.last_activity = now
        if now < self.locked_until:
            return False
        del self.failures[: bisect_right(self.failures, now - _MINUTE_SECONDS)]
        self.failures.append(now)
        if len(self.failures) < _FAILURES_PER_MINUTE:
            return False
        self.failures.clear()
        self.locked_until = now + _MINUTE_SECONDS
        return True


def _source_key(scope: Scope) -> str:
    client = scope.get("client")
    try:
        address = ip_address(client[0] if client else "")
    except ValueError:
        return "unknown"
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(IPv6Network((address, 64), strict=False))
    return str(address)


class _AttemptTable:
    def __init__(self, capacity: int) -> None:
        self._entries: OrderedDict[str, _Attempts] = OrderedDict()
        self._overflow = [_Attempts() for _ in range(min(_OVERFLOW_BUCKETS, capacity))]
        self._capacity = capacity - len(self._overflow)

    def _overflow_for(self, key: str) -> _Attempts:
        return self._overflow[hash(key) % len(self._overflow)]

    def admit(self, key: str, per_minute: int, per_hour: int, now: float) -> _Attempts:
        while self._entries and next(iter(self._entries.values())).last_activity + _HOUR_SECONDS <= now:
            self._entries.popitem(last=False)
        attempts = self._entries.get(key)
        if attempts is None:
            overflow = self._overflow_for(key)
            # 已使用的共享额度须自然过期，释放独占条目不能让同一来源提前重获配额。
            if len(self._entries) >= self._capacity or overflow.last_activity + _HOUR_SECONDS > now:
                overflow.reserve(now, per_minute, per_hour)
                return overflow
            attempts = _Attempts()
            self._entries[key] = attempts
        attempts.reserve(now, per_minute, per_hour)
        self._entries.move_to_end(key)
        return attempts

    def record_failure(self, key: str, attempts: _Attempts) -> bool:
        current = self._entries.get(key)
        if current is attempts:
            self._entries.move_to_end(key)
        elif current is not None or self._overflow_for(key) is not attempts:
            return False
        return attempts.record_failure(monotonic())

    def release(self, key: str, attempts: _Attempts, started_at: float) -> None:
        index = bisect_right(attempts.requests, started_at) - 1
        if index >= 0 and attempts.requests[index] == started_at:
            attempts.requests.pop(index)
        if not attempts.requests and not attempts.failures and attempts.locked_until <= monotonic():
            if self._entries.get(key) is attempts:
                del self._entries[key]
            attempts.last_activity = float("-inf")


def _issued_credential_key(request: Request, path: str) -> str | None:
    """识别服务端签发的凭据以分配查验额度，吊销与会话归属仍由端点鉴权。"""
    claims = rate_limit_bearer_claims(request)
    if claims is not None:
        subject, jti = claims.get("sub"), claims.get("jti")
        if (
            isinstance(subject, str)
            and subject.isascii()
            and subject.isdecimal()
            and isinstance(jti, str)
            and jti
            and claims.get("purpose") is None
        ):
            return f"bearer:{jti}"
    elif path == _TICKET_PATH:
        token = request.cookies.get(remote_session_cookie().name)
        if (
            token
            and len(token) <= REMOTE_TOKEN_MAX_LENGTH
            and valid_remote_csrf(token, request.headers.get("x-csrf-token"))
        ):
            return f"cookie:{hash_remote_token(token)}"
    return None


def limit_authenticated_session(request: Request, session_key: str) -> None:
    """只在登录记录或设备通过完整鉴权后调用，JWT 轮换继续使用同一份额度。"""
    table: _AttemptTable = request.state.authenticated_session_limits
    table.admit(session_key, _SESSION_PER_MINUTE, _SESSION_PER_HOUR, monotonic())


class AuthenticationRateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._sources = {
            path: _AttemptTable(_MAX_SOURCES_PER_PATH // 2 if path in _SESSION_PATHS else _MAX_SOURCES_PER_PATH)
            for path in AUTHENTICATION_PATHS
        }
        self._credentials = {path: _AttemptTable(_MAX_SOURCES_PER_PATH // 4) for path in _SESSION_PATHS}
        self._sessions = {path: _AttemptTable(_MAX_SOURCES_PER_PATH // 4) for path in _SESSION_PATHS}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and (path == root_path or path.startswith(f"{root_path}/")):
            path = path[len(root_path) :]
        table = self._sources.get(path)
        if table is None:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        source = key = _source_key(scope)
        per_minute, per_hour = SETTINGS.login_rate_limit_per_minute, _ATTEMPTS_PER_HOUR
        session_path = path in _SESSION_PATHS
        if session_path:
            request.state.authenticated_session_limits = self._sessions[path]
            if credential := _issued_credential_key(request, path):
                table, key = self._credentials[path], credential
                per_minute, per_hour = _SESSION_PER_MINUTE, _SESSION_PER_HOUR
        started_at = monotonic()
        try:
            attempts = table.admit(key, per_minute, per_hour, started_at)
        except HTTPException as exc:
            response = await http_exception_handler(request, exc)
            await response(scope, receive, send)
            return

        async def account_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = message["status"]
                if session_path and (200 <= status < 300 or status == 429):
                    # 已通过鉴权的请求由稳定会话额度结算，收回查验时预占的次数。
                    table.release(key, attempts, started_at)
                elif 400 <= status < 500 and status != 429 and table.record_failure(key, attempts):
                    logger.warning("Repeated authentication failures: %s from %s", path, source)
            await send(message)

        await self.app(scope, receive, account_response)
