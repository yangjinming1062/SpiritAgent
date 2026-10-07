import ipaddress
import socket
from collections.abc import Iterable
from functools import partial
from typing import Any
from urllib.parse import urljoin, urlparse

import anyio
import httpcore
import httpx

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)

BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata",
        "instance-data.ec2.internal",
        "instance-data",
        "kubernetes.default.svc",
    },
)
_BLOCKED_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BLOCKED_CLOUD_META = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),  # 阿里云
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IPv6
    },
)


def _ip_in_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _BLOCKED_CLOUD_META or ip in _BLOCKED_CGNAT


def _ssrf_allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """运维声明的 IP 段豁免保留段拒绝——给 fake-ip TUN 代理（Clash 等）的逃生口；云元数据 / CGNAT 块与 hostname 黑名单不受此豁免影响。"""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in (SETTINGS.ssrf_allowed_cidrs or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("SSRF_ALLOWED_CIDRS: ignoring unparseable CIDR", extra={"cidr": part})
    return networks


def _evaluate_ip(ip_str: str) -> tuple[bool, str]:
    """根据当前 SSRF 策略评估单个 IP。返回 (allowed, reason)。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False, f"unparseable address {ip_str!r}"
    # IPv4 映射的 IPv6（::ffff:a.b.c.d）实际连到对应 IPv4，按 IPv4 规则判定
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if _ip_in_blocked(ip):
        return False, f"refusing to connect to {ip_str} (cloud-metadata / CGNAT)"

    allowed = _ssrf_allowed_networks()
    if any(ip in network for network in allowed):
        return True, ""

    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return (False, f"refusing to connect to {ip_str} (loopback/link-local/private/multicast)")
    return True, ""


def _evaluate_hostname(host: str) -> tuple[bool, str]:
    """对 hostname 走黑名单快速判定。IP 字面量的策略评估走 ``_evaluate_ip``。"""
    if not host:
        return False, "missing host"
    if host.lower() in BLOCKED_HOSTNAMES:
        return False, f"refusing to connect to blocked hostname {host!r}"
    return True, ""


def _resolve_and_validate(host: str, port: int) -> list[tuple[str, int]]:
    """同步 DNS 解析并校验全部结果；返回通过的 (ip, port)。async 路径由调用方移出事件循环。"""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"DNS resolution failed for {host}: {exc}") from exc

    if not infos:
        raise httpcore.ConnectError(f"No addresses returned for {host}")

    allowed: list[tuple[str, int]] = []
    for info in infos:
        ip_str = info[4][0]
        ok, reason = _evaluate_ip(ip_str)
        if not ok:
            logger.warning("SSRF guard blocked %s -> %s: %s", host, ip_str, reason)
            raise httpcore.ConnectError(f"refusing to connect to {ip_str} ({reason})")
        allowed.append((ip_str, port))
    return allowed


class _SafeOutboundAsyncBackend(httpcore._backends.auto.AutoBackend):
    """出站 SSRF 守卫后端：DNS 在工作线程解析，每个目标 IP 在 socket.connect 前过策略，以已校验 IP 直连（Host/SNI/证书仍用原 host）；重定向每跳重走 connect_tcp。守卫关闭时行为与默认后端一致。"""

    async def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore._backends.base.AsyncNetworkStream:
        if not SETTINGS.ssrf_guard_enabled:
            return await super().connect_tcp(
                host,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        # hostname 黑名单在进 DNS 之前先判，省一次解析与工作线程。
        ok, reason = _evaluate_hostname(host)
        if not ok:
            logger.warning("SSRF guard refused %s: %s", host, reason)
            raise httpcore.ConnectError(f"refusing to connect to {host} ({reason})")

        validated = await anyio.to_thread.run_sync(partial(_resolve_and_validate, host, port))

        await self._init_backend()
        last_error: Exception | None = None
        for ip_str, resolved_port in validated:
            try:
                return await self._backend.connect_tcp(
                    ip_str,
                    resolved_port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (OSError, TimeoutError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError(f"No validated addresses available for connect to {host}")

    async def connect_unix_socket(  # type: ignore[override]
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore._backends.base.AsyncNetworkStream:
        await self._init_backend()
        return await self._backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)


class _SafeOutboundAsyncTransport(httpx.AsyncHTTPTransport):
    """挂载 ``_SafeOutboundAsyncBackend`` 的 httpx 传输层；``follow_redirects=False`` 与默认一致，逐跳校验交给 ``download_capped``。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # httpcore 私有属性；连接池其余配置（limits / ssl_context）由 super 接管，不重置。
        self._pool._network_backend = _SafeOutboundAsyncBackend()


def safe_outbound_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """带建连期 SSRF 守卫的 AsyncClient 工厂；不跟随重定向，``download_capped`` 自行逐跳校验。"""
    return httpx.AsyncClient(transport=safe_outbound_async_transport(), **kwargs)


def safe_outbound_async_transport() -> httpx.AsyncBaseTransport:
    """返回带 SSRF 守卫的 transport（不附带 client）——给需要在 transport 外面再包一层（如重试 / 自定义 pool）的调用方使用。"""
    return _SafeOutboundAsyncTransport()


_MAX_REDIRECTS = 5


async def download_capped(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """下载远程 URL：大小上限、逐跳 SSRF（由建连后端完成）、协议白名单 {http, https} 与 HTTPS→HTTP 降级防护。"""
    current_url = url
    redirect_count = 0

    client_timeout = httpx.Timeout(timeout, connect=10.0, read=timeout, write=timeout)

    while True:
        parsed = urlparse(current_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")

        async with (
            safe_outbound_async_client(timeout=client_timeout) as client,
            client.stream("GET", current_url) as resp,
        ):
            if resp.is_redirect:
                redirect_count += 1
                if redirect_count > _MAX_REDIRECTS:
                    raise RuntimeError(f"too many redirects ({redirect_count} > {_MAX_REDIRECTS})")

                location = resp.headers.get("location")
                if not location:
                    raise RuntimeError("redirect response missing location header")

                target_url = urljoin(current_url, location)
                target_parsed = urlparse(target_url)

                if target_parsed.scheme not in ("http", "https"):
                    raise ValueError(f"redirect to unsupported scheme: {target_parsed.scheme!r}")

                if parsed.scheme == "https" and target_parsed.scheme == "http":
                    raise ValueError("refusing redirect downgrade from HTTPS to HTTP")

                current_url = target_url
                continue

            resp.raise_for_status()

            sink = bytearray()
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"download exceeded size limit of {max_bytes} bytes")
                sink.extend(chunk)

            return bytes(sink)
