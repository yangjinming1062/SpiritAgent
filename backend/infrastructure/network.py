import asyncio
import ipaddress
import re
import socket
import unicodedata
from collections.abc import Iterable
from contextlib import AsyncExitStack
from functools import partial
from typing import Any
from urllib.parse import urljoin, urlsplit

import anyio
import httpcore
import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus

from infrastructure.config import BACKEND_DIR

_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data.ec2.internal",
        "instance-data",
        "kubernetes.default.svc",
    }
)
_METADATA_IPS = frozenset({ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254")})
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_URL_HOST = re.compile(rb"[A-Za-z0-9._:-]+")
_SOURCE_URL_HOST = re.compile(r"^(https?://)([^:/?#@\[\]]+)(?=[:/?#]|$)", re.IGNORECASE)
_LEGACY_IPV4_HOST = re.compile(r"(?:[0-9]+|0x[0-9a-f]*)(?:\.(?:[0-9]+|0x[0-9a-f]*))*")
_DNS_DOTS = str.maketrans("\u3002\uff0e\uff61", "...")
_CONNECT_TIMEOUT = 10.0
_MAX_REDIRECTS = 5

type _IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type _IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type _Origin = tuple[str, str, int]
type _ProxyBypassPattern = tuple[str, str, int | None]


def _check_ip(address: _IPAddress, *, public_only: bool) -> None:
    if isinstance(address, ipaddress.IPv6Address):
        if embedded := address.ipv4_mapped or address.sixtofour:
            address = embedded
        elif address in _NAT64_NETWORK:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address in _METADATA_IPS or address.is_link_local:
        raise ValueError("Cloud metadata and link-local addresses are not allowed")
    if address.is_unspecified or address.is_multicast or (address.is_reserved and not address.is_loopback):
        raise ValueError("Reserved network addresses are not allowed")
    if public_only and (not address.is_global or isinstance(address, ipaddress.IPv6Address) and address.is_site_local):
        raise ValueError("Only public network addresses are allowed for this URL")


def _check_host(host: str, *, public_only: bool) -> _IPAddress | None:
    normalized = host.lower().rstrip(".")
    if normalized in _BLOCKED_HOSTNAMES:
        raise ValueError("Cloud metadata and internal service hostnames are not allowed")
    if public_only and (normalized == "localhost" or normalized.endswith(".localhost")):
        raise ValueError("Only public network addresses are allowed for this URL")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if _LEGACY_IPV4_HOST.fullmatch(normalized):
            raise ValueError("IPv4 addresses must use canonical dotted decimal notation") from None
        return None
    _check_ip(address, public_only=public_only)
    return address


def _absolute_url(value: str | httpx.URL, schemes: tuple[str, ...]) -> httpx.URL:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ValueError("Invalid outbound URL") from None
    if (
        url.scheme not in schemes
        or not _URL_HOST.fullmatch(url.raw_host)
        or url.userinfo
        or (url.port is not None and not 0 < url.port <= 65535)
    ):
        raise ValueError("An absolute URL with a supported scheme and no embedded credentials is required")
    return url


def _url_origin(url: httpx.URL) -> _Origin:
    host = url.raw_host.decode("ascii")
    if ":" in host:
        host = str(ipaddress.IPv6Address(host))
    return url.scheme, host, url.port or (443 if url.scheme in {"https", "wss"} else 80)


def _service_origin(base_url: str) -> _Origin:
    url = _absolute_url(base_url, ("http", "https"))
    if "?" in base_url or "#" in base_url:
        raise ValueError("Configured service URLs cannot include queries or fragments")
    origin = _url_origin(url)
    _check_host(origin[1], public_only=False)
    return origin


def _resolve_addresses(host: str, port: int, *, public_only: bool) -> list[str]:
    if address := _check_host(host, public_only=public_only):
        return [str(address)]
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise httpcore.ConnectError("Outbound host could not be resolved") from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise httpcore.ConnectError("Outbound host returned no addresses")
    for address in addresses:
        _check_ip(ipaddress.ip_address(address), public_only=public_only)
    return addresses


async def _resolve(host: str, port: int, *, public_only: bool) -> list[str]:
    return await anyio.to_thread.run_sync(
        partial(_resolve_addresses, host, port, public_only=public_only), abandon_on_cancel=True
    )


def _address_attempt_timeout(deadline: float, attempts_left: int) -> float:
    return max(0.0, deadline - anyio.current_time()) / attempts_left


class _CheckedNetworkBackend(httpcore.AnyIOBackend):
    """只拨已校验的地址，HTTP Host 和 TLS 仍使用请求的原始主机。"""

    def __init__(self, *, public_only: bool) -> None:
        self._public_only = public_only

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        options = tuple(socket_options or ())
        try:
            with anyio.fail_after(timeout) as deadline:
                addresses = await _resolve(host, port, public_only=self._public_only)
                errors: list[Exception] = []
                for index, address in enumerate(addresses):
                    attempt_timeout = (
                        _address_attempt_timeout(deadline.deadline, len(addresses) - index)
                        if timeout is not None
                        else None
                    )
                    try:
                        return await super().connect_tcp(
                            address, port, timeout=attempt_timeout, local_address=local_address, socket_options=options
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                        errors.append(exc)
                raise errors[-1]
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("DNS validation or connection timed out") from exc


class _CheckedAsyncTransport(httpx.AsyncHTTPTransport):
    """直连时使用已校验的地址。"""

    def __init__(self, *, public_only: bool, trust_env: bool) -> None:
        super().__init__(trust_env=trust_env)
        self._pool._network_backend = _CheckedNetworkBackend(public_only=public_only)


class _ProxySettings(BaseSettings):
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""
    no_proxy: str = ""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


def _proxy_bypass_config(
    no_proxy: str,
) -> tuple[bool, list[_ProxyBypassPattern], list[_IPNetwork]]:
    entries = [entry.strip() for entry in no_proxy.split(",")]
    bypass_all = "*" in entries
    patterns: list[_ProxyBypassPattern] = []
    networks: list[_IPNetwork] = []
    for entry in entries:
        if bypass_all:
            break
        if not entry:
            continue
        if "/" in entry and "://" not in entry:
            networks.append(ipaddress.ip_network(entry, strict=False))
            continue
        if "://" in entry:
            pattern = entry
        else:
            try:
                address = ipaddress.ip_address(entry.strip("[]"))
            except ValueError:
                pattern = f"all://{entry if entry.startswith(('*', '[')) else '*' + entry}"
            else:
                networks.append(ipaddress.ip_network(str(address)))
                continue
        parsed = urlsplit(pattern)
        original_host = parsed.hostname or ""
        host = original_host.translate(_DNS_DOTS).rstrip(".")
        if host in {"", "*"} and host != original_host:
            continue
        wildcard = "*." if host.startswith("*.") else "*" if host.startswith("*") else ""
        name = host[len(wildcard) :]
        if ":" in name:
            name = str(ipaddress.IPv6Address(name))
        elif name:
            name = httpx.URL(host=unicodedata.normalize("NFC", name)).raw_host.decode("ascii")
        patterns.append((parsed.scheme, wildcard + name, parsed.port))
    return bypass_all, patterns, networks


def _proxy_host_matches(host: str, pattern: str) -> bool:
    if pattern in {"", "*"}:
        return True
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    if pattern.startswith("*"):
        name = pattern[1:]
        return host == name or host.endswith(f".{name}")
    return host == pattern


def _proxy_bypassed(
    url: httpx.URL,
    bypass_all: bool,
    patterns: list[_ProxyBypassPattern],
    networks: list[_IPNetwork],
) -> bool:
    if bypass_all:
        return True
    scheme, host, port = _url_origin(url)
    host = host.rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        if any(address in network or (mapped is not None and mapped in network) for network in networks):
            return True
    return any(
        pattern_scheme in {"all", scheme}
        and (pattern_port is None or pattern_port == port)
        and _proxy_host_matches(host, pattern)
        for pattern_scheme, pattern, pattern_port in patterns
    )


def _websocket_proxy(endpoint: httpx.URL) -> str | None:
    proxies = _ProxySettings().model_dump()
    http_scheme = "https" if endpoint.scheme == "wss" else "http"
    bypass = _proxy_bypass_config(proxies.get("no_proxy", ""))
    if _proxy_bypassed(endpoint, *bypass) or _proxy_bypassed(endpoint.copy_with(scheme=http_scheme), *bypass):
        return None
    for key in ("https_proxy", "http_proxy", "all_proxy"):
        if proxy := proxies.get(key):
            return proxy if "://" in proxy else f"http://{proxy}"
    return None


class _ServiceWebSocketConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception | str:
        if not isinstance(exc, InvalidStatus) or exc.response.status_code not in {300, 301, 302, 303, 307, 308}:
            return exc
        locations = exc.response.headers.get_all("Location")
        if len(locations) != 1 or not locations[0].strip():
            return exc
        try:
            redirect = urljoin(self.uri, locations[0])
            source = _absolute_url(self.uri, ("ws", "wss"))
            target = _absolute_url(redirect, ("ws", "wss"))
        except ValueError:
            return exc
        return redirect if _url_origin(target) == _url_origin(source) else exc


async def connect_service_websocket(url: str, *, headers: dict[str, str], open_timeout: float) -> ClientConnection:
    endpoint = _absolute_url(url, ("ws", "wss"))
    _, host, port = _url_origin(endpoint)
    _check_host(host, public_only=False)
    if proxy := _websocket_proxy(endpoint):
        return await _ServiceWebSocketConnect(
            str(endpoint), additional_headers=headers, open_timeout=open_timeout, proxy=proxy
        )

    with anyio.fail_after(open_timeout) as deadline:
        addresses = await _resolve(host, port, public_only=False)
        errors: list[OSError] = []
        for index, address in enumerate(addresses):
            try:
                return await _ServiceWebSocketConnect(
                    str(endpoint),
                    additional_headers=headers,
                    open_timeout=_address_attempt_timeout(deadline.deadline, len(addresses) - index),
                    proxy=None,
                    host=address,
                )
            except OSError as exc:
                errors.append(exc)
        raise errors[-1]


class _OutboundAsyncTransport(httpx.AsyncBaseTransport):
    """按环境代理和 NO_PROXY 路由，网段只匹配 URL 中的字面 IP。"""

    def __init__(self, origins: frozenset[_Origin] | None, *, trust_env: bool) -> None:
        self._origins = origins
        proxies = _ProxySettings().model_dump() if trust_env else {}
        self._bypass_all, self._bypass_patterns, self._bypass_networks = _proxy_bypass_config(
            proxies.get("no_proxy", "")
        )

        self._direct = _CheckedAsyncTransport(public_only=origins is None, trust_env=trust_env)
        self._proxies: dict[str, httpx.AsyncHTTPTransport] = {}
        if not self._bypass_all:
            for scheme in ("http", "https", "all"):
                if proxy := proxies.get(f"{scheme}_proxy"):
                    proxy_url = httpx.URL(proxy if "://" in proxy else f"http://{proxy}")
                    tls_context = httpx.create_ssl_context(trust_env=trust_env) if proxy_url.scheme == "https" else None
                    self._proxies[scheme] = httpx.AsyncHTTPTransport(
                        trust_env=trust_env, proxy=httpx.Proxy(proxy_url, ssl_context=tls_context)
                    )

    def _bypass_proxy(self, url: httpx.URL) -> bool:
        return _proxy_bypassed(url, self._bypass_all, self._bypass_patterns, self._bypass_networks)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = _absolute_url(request.url, ("http", "https"))
        origin = _url_origin(url)
        if self._origins is None:
            _check_host(origin[1], public_only=True)
        elif origin not in self._origins:
            raise ValueError("Configured service requests must remain on their operator-approved origin")
        transport = self._direct
        if not self._bypass_proxy(url):
            transport = self._proxies.get(url.scheme) or self._proxies.get("all") or self._direct
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        results = await asyncio.gather(
            *(transport.aclose() for transport in (self._direct, *self._proxies.values())), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result


def validate_public_source_url(url: str) -> None:
    """校验交给提取服务的 URL，实际解析、连接和重定向由提取服务约束。"""
    url = url.strip()
    if not url.isascii():
        try:
            url = _SOURCE_URL_HOST.sub(lambda match: match[1] + match[2].encode("idna").decode("ascii"), url, count=1)
        except UnicodeError:
            raise ValueError("Invalid source hostname") from None
    target = _absolute_url(url, ("http", "https"))
    _check_host(target.raw_host.decode("ascii"), public_only=True)


def trusted_service_async_transport(*base_urls: str, trust_env: bool = True) -> httpx.AsyncBaseTransport:
    return _OutboundAsyncTransport(frozenset(_service_origin(url) for url in base_urls), trust_env=trust_env)


def trusted_service_async_client(
    base_url: str, *additional_base_urls: str, trust_env: bool = True, **kwargs: Any
) -> httpx.AsyncClient:
    """仅接受固定服务源；不自动跟随重定向。"""
    if kwargs.pop("follow_redirects", False):
        raise ValueError("Configured services cannot follow redirects")
    transport = trusted_service_async_transport(base_url, *additional_base_urls, trust_env=trust_env)
    return httpx.AsyncClient(
        base_url=base_url, transport=transport, trust_env=trust_env, follow_redirects=False, **kwargs
    )


def ai_http_timeout() -> httpx.Timeout:
    """模型响应与媒体传输持续等待；连接建立和连接池等待仍有界。"""
    return httpx.Timeout(None, connect=_CONNECT_TIMEOUT, pool=_CONNECT_TIMEOUT)


async def read_capped_response(response: httpx.Response, *, max_bytes: int, too_large: Exception) -> bytes:
    """有界读取响应体；响应的关闭与错误翻译由调用方负责。"""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise too_large
        body.extend(chunk)
    return bytes(body)


async def download_capped(
    url: str, *, max_bytes: int, timeout: float | None = None, service_base_url: str | None = None
) -> bytes:
    """有界下载。成品从冻结端点的同源地址开始时可使用该服务的连接策略。"""
    current = _absolute_url(url, ("http", "https"))
    origin = _url_origin(current)
    origins = (
        frozenset({origin}) if service_base_url is not None and _service_origin(service_base_url) == origin else None
    )
    client_timeout = httpx.Timeout(timeout, connect=_CONNECT_TIMEOUT, pool=_CONNECT_TIMEOUT)
    async with AsyncExitStack() as stack:

        async def open_client(allowed_origins: frozenset[_Origin] | None) -> httpx.AsyncClient:
            transport = _OutboundAsyncTransport(allowed_origins, trust_env=True)
            return await stack.enter_async_context(httpx.AsyncClient(transport=transport, timeout=client_timeout))

        client = await open_client(origins)
        for hop in range(_MAX_REDIRECTS + 1):
            async with client.stream("GET", current) as response:
                if not response.is_redirect:
                    response.raise_for_status()
                    return await read_capped_response(
                        response,
                        max_bytes=max_bytes,
                        too_large=ValueError(f"download exceeded size limit of {max_bytes} bytes"),
                    )
                if hop == _MAX_REDIRECTS:
                    break
                location = response.headers.get("location")
                if not location or not location.strip():
                    raise ValueError("Download redirect is missing its location")
                try:
                    target = _absolute_url(current.join(location), ("http", "https"))
                except httpx.InvalidURL:
                    raise ValueError("Invalid download redirect URL") from None
                if current.scheme == "https" and target.scheme == "http":
                    raise ValueError("Refusing redirect downgrade from HTTPS to HTTP")
            current = target
            client.cookies.clear()
            # 跨源后继续执行公网策略，后续重定向也不会恢复服务信任。
            if origins is not None and _url_origin(current) not in origins:
                client = await open_client(None)
                origins = None
        raise ValueError(f"Download exceeded the redirect limit of {_MAX_REDIRECTS}")
