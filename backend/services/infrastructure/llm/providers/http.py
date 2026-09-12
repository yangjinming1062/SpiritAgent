import asyncio
import base64
import contextlib
import random
import threading
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
from components import SETTINGS, get_logger, safe_outbound_async_transport
from openai import AsyncOpenAI, NotGiven

from .base import ProviderResultUnknownError

logger = get_logger(__name__)

# 捕获传输错误后仅重试幂等请求或确认未发送的连接失败；非幂等请求的响应丢失会转为结果不确定。
# HTTPStatusError 不在内——已经收到底层 HTTP 响应，由供应商错误分类决定后续动作。
_RETRYABLE_TRANSPORT_EXC: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.NetworkError,
)
_SAFE_BEFORE_SEND_EXC: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# 请求校验失败模式：请求畸形，每次重试结果相同；某些 OpenAI 兼容网关（如 codex.nekos.me）会把它当作 5xx 返回，会让通用 "5xx → 可重试" 规则误触发重试风暴。命中后归为不可重试的 format_error，快速失败并回退。
_REQUEST_VALIDATION_PATTERNS = (
    "unknown parameter",
    "unsupported parameter",
    "unrecognized request argument",
    "invalid_request_error",
    "unknown_parameter",
    "unsupported_parameter",
)


async def download_as_b64(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")


class _RetryAsyncTransport(httpx.AsyncBaseTransport):
    """透明包装 ``inner``，只重放不会重复产生供应商副作用的请求。"""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        max_retries: int,
        base_delay: float,
        max_delay: float,
    ) -> None:
        self._inner = inner
        self._max_attempts = max(1, max_retries + 1)
        self._base_delay = max(0.0, base_delay)
        self._max_delay = max(self._base_delay, max_delay)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        last_exc: BaseException | None = None
        for attempt in range(self._max_attempts):
            current = _clone_request(request, body) if attempt > 0 else request
            try:
                return await self._inner.handle_async_request(current)
            except _RETRYABLE_TRANSPORT_EXC as exc:
                last_exc = exc
                if not _can_retry(request, exc):
                    logger.warning(
                        "provider http result unknown; automatic retry suppressed",
                        extra={
                            "method": request.method,
                            "url": str(request.url),
                            "error": type(exc).__name__,
                        },
                    )
                    raise ProviderResultUnknownError(
                        request.method,
                        str(request.url),
                    ) from exc
                if attempt + 1 >= self._max_attempts:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                # ±25% 抖动缓解 thundering herd；非负后下界 0。
                sleep_for = max(0.0, delay + delay * random.uniform(-0.25, 0.25))
                logger.warning(
                    "provider http transient error; retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_attempts": self._max_attempts,
                        "delay_s": round(sleep_for, 2),
                        "method": request.method,
                        "url": str(request.url),
                        "error": type(exc).__name__,
                    },
                )
                await asyncio.sleep(sleep_for)
        assert last_exc is not None  # loop entered only via except branch
        raise last_exc

    async def aclose(self) -> None:
        await self._inner.aclose()


def _can_retry(request: httpx.Request, exc: BaseException) -> bool:
    return (
        request.method.upper() in _IDEMPOTENT_METHODS
        or "Idempotency-Key" in request.headers
        or isinstance(exc, _SAFE_BEFORE_SEND_EXC)
    )


def _clone_request(request: httpx.Request, body: bytes) -> httpx.Request:
    return httpx.Request(
        method=request.method,
        url=request.url,
        headers=request.headers,
        content=body,
        extensions=request.extensions,
    )


@dataclass(frozen=True)
class _ClientSettings:
    request_timeout: float
    max_retries: int
    base_retry_delay: float
    max_retry_delay: float


class _ClientGeneration:
    def __init__(self, settings: _ClientSettings) -> None:
        self.settings = settings
        self.clients: dict[_RotatingAsyncClient, httpx.AsyncClient] = {}
        self.active = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.closed = False

    def acquire(self) -> None:
        if self.closed:
            raise RuntimeError("LLM HTTP client generation is closed")
        self.active += 1
        self.idle.clear()

    def release(self) -> None:
        self.active -= 1
        if self.active == 0:
            self.idle.set()


class _GenerationStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        release: Callable[[], None],
    ) -> None:
        self._stream = stream
        self._release = release
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._stream:
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.aclose()
        finally:
            self._release()


class _UnusedFrontendTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, _request: httpx.Request) -> httpx.Response:
        raise RuntimeError("rotating client frontend cannot send directly")


class _RotatingAsyncClient(httpx.AsyncClient):
    def __init__(
        self,
        *,
        transport_factory: Callable[[_ClientSettings], httpx.AsyncBaseTransport],
        **kwargs: Any,
    ) -> None:
        self._transport_factory = transport_factory
        super().__init__(transport=_UnusedFrontendTransport(), **kwargs)

    def build_delegate(self, settings: _ClientSettings) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=_http_timeout(settings),
            transport=self._transport_factory(settings),
        )

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        if self.is_closed:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        generation, client = _delegate_for(self)
        release_here = True
        try:
            response = await client.send(
                request,
                stream=stream,
                auth=auth,
                follow_redirects=follow_redirects,
            )
            if stream:
                response.stream = _GenerationStream(
                    response.stream,
                    lambda: _release_generation(generation),
                )
                release_here = False
            return response
        finally:
            if release_here:
                _release_generation(generation)


_cache_lock = threading.Lock()
_pinned_generation: ContextVar[_ClientGeneration | None] = ContextVar(
    "llm_http_generation",
    default=None,
)
_clients: dict[tuple[str, str], _RotatingAsyncClient] = {}
_clients_openai: dict[tuple[str, str], AsyncOpenAI] = {}
_retirement_tasks: set[asyncio.Task[None]] = set()
_closing = False


def _settings_snapshot() -> _ClientSettings:
    return _ClientSettings(
        request_timeout=float(SETTINGS.llm_request_timeout_seconds),
        max_retries=max(0, int(SETTINGS.llm_max_retry_attempts)),
        base_retry_delay=float(SETTINGS.llm_base_retry_delay),
        max_retry_delay=float(SETTINGS.llm_max_retry_delay),
    )


_generation = _ClientGeneration(_settings_snapshot())


def _http_timeout(settings: _ClientSettings) -> httpx.Timeout:
    return httpx.Timeout(settings.request_timeout, connect=10.0)


def _openai_timeout(settings: _ClientSettings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=10.0,
        read=settings.request_timeout,
        write=settings.request_timeout,
        pool=10.0,
    )


def _raw_transport(settings: _ClientSettings) -> httpx.AsyncBaseTransport:
    return _RetryAsyncTransport(
        safe_outbound_async_transport(),
        max_retries=settings.max_retries,
        base_delay=settings.base_retry_delay,
        max_delay=settings.max_retry_delay,
    )


def _openai_transport(_settings: _ClientSettings) -> httpx.AsyncBaseTransport:
    return safe_outbound_async_transport()


def _acquire_generation(
    generation: _ClientGeneration | None = None,
) -> _ClientGeneration:
    with _cache_lock:
        if generation is None:
            if _closing:
                raise RuntimeError("LLM HTTP clients are closing")
            generation = _generation
        generation.acquire()
        return generation


def _release_generation(generation: _ClientGeneration) -> None:
    with _cache_lock:
        generation.release()


def _delegate_for(
    frontend: _RotatingAsyncClient,
) -> tuple[_ClientGeneration, httpx.AsyncClient]:
    generation = _acquire_generation(_pinned_generation.get())
    try:
        with _cache_lock:
            client = generation.clients.get(frontend)
            if client is None:
                client = frontend.build_delegate(generation.settings)
                generation.clients[frontend] = client
            return generation, client
    except BaseException:
        _release_generation(generation)
        raise


async def _close_generation(generation: _ClientGeneration) -> None:
    while True:
        await generation.idle.wait()
        with _cache_lock:
            if generation.active:
                continue
            if generation.closed:
                return
            generation.closed = True
            clients = list(generation.clients.values())
            generation.clients.clear()
            break
    for client in clients:
        with contextlib.suppress(Exception):
            await client.aclose()


def _forget_retirement_task(task: asyncio.Task[None]) -> None:
    with _cache_lock:
        _retirement_tasks.discard(task)


def _retire_generation(generation: _ClientGeneration) -> None:
    with _cache_lock:
        if not generation.active and not generation.clients:
            generation.closed = True
            return
    task = asyncio.create_task(
        _close_generation(generation),
        name="llm-http-client-retirement",
    )
    with _cache_lock:
        _retirement_tasks.add(task)
    task.add_done_callback(_forget_retirement_task)


def rotate_http_clients() -> None:
    """切换到当前配置对应的新连接池，并在旧代请求结束后关闭旧池。"""

    global _generation
    settings = _settings_snapshot()
    with _cache_lock:
        if _closing:
            raise RuntimeError("LLM HTTP clients are closing")
        retiring = _generation
        _generation = _ClientGeneration(settings)
        for client in _clients.values():
            client.timeout = _http_timeout(settings)
    _retire_generation(retiring)


async def aclose_all() -> None:
    global _closing, _generation
    with _cache_lock:
        _closing = True
        retiring = _generation
        _generation = _ClientGeneration(_settings_snapshot())
        clients = list(_clients.values())
        clients_openai = list(_clients_openai.values())
        _clients.clear()
        _clients_openai.clear()
    _retire_generation(retiring)
    try:
        while True:
            with _cache_lock:
                tasks = list(_retirement_tasks)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)
        for client_openai in clients_openai:
            with contextlib.suppress(Exception):
                await client_openai.close()
        for client in clients:
            with contextlib.suppress(Exception):
                await client.aclose()
    finally:
        with _cache_lock:
            _closing = False


class _RetryAwareAsyncOpenAI(AsyncOpenAI):
    """在 SDK 默认 ``_should_retry`` 之外拦截 500/502 中的请求校验错误（畸形请求每次重试都失败），其余决策完全继承父类。"""

    def _should_retry(
        self,
        response: httpx.Response,
        *args: Any,
        **kwargs: Any,
    ) -> bool:  # type: ignore[override]
        if response.status_code in (500, 502):
            body = response.text or ""
            if body and any(pattern in body for pattern in _REQUEST_VALIDATION_PATTERNS):
                return False
        return super()._should_retry(response, *args, **kwargs)

    async def request(
        self,
        cast_to: type[Any],
        options: Any,
        *,
        stream: bool = False,
        stream_cls: type[Any] | None = None,
    ) -> Any:  # type: ignore[override]
        generation = _acquire_generation()
        token = _pinned_generation.set(generation)
        updates: dict[str, Any] = {}
        if isinstance(options.max_retries, NotGiven):
            updates["max_retries"] = generation.settings.max_retries
        if isinstance(options.timeout, NotGiven):
            updates["timeout"] = _openai_timeout(generation.settings)
        if updates:
            options = options.model_copy(update=updates)
        try:
            return await super().request(
                cast_to,
                options,
                stream=stream,
                stream_cls=stream_cls,
            )
        finally:
            _pinned_generation.reset(token)
            _release_generation(generation)


def get_http(
    base_url: str,
    api_key: str,
    *,
    auth_header: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """按 (base_url, api_key) 缓存 httpx 客户端；auth_header 可覆盖默认 Authorization。"""
    key = (base_url.rstrip("/"), api_key)
    with _cache_lock:
        if _closing:
            raise RuntimeError("LLM HTTP clients are closing")
        client = _clients.get(key)
        if client is not None:
            return client
        headers = (
            {"Authorization": f"Bearer {api_key}"}
            if auth_header is None
            else {name: value.format(api_key=api_key) for name, value in auth_header.items()}
        )
        client = _RotatingAsyncClient(
            base_url=key[0],
            timeout=_http_timeout(_generation.settings),
            headers=headers,
            follow_redirects=False,
            transport_factory=_raw_transport,
        )
        _clients[key] = client
        return client


def get_async_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """按 (api_key, base_url) 缓存 AsyncOpenAI 客户端。"""
    key = (api_key, base_url.rstrip("/"))
    with _cache_lock:
        if _closing:
            raise RuntimeError("LLM HTTP clients are closing")
        client = _clients_openai.get(key)
        if client is not None:
            return client
        settings = _generation.settings
        http_client = _RotatingAsyncClient(
            timeout=_openai_timeout(settings),
            transport_factory=_openai_transport,
        )
        client = _RetryAwareAsyncOpenAI(
            api_key=api_key,
            base_url=key[1],
            max_retries=settings.max_retries,
            timeout=_openai_timeout(settings),
            http_client=http_client,
        )
        _clients_openai[key] = client
        return client
