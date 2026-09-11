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
_BLOCKED_ALIBABA_META = ipaddress.ip_address("100.100.100.200")
_BLOCKED_AWS_META_IPV6 = ipaddress.ip_address("fd00:ec2::254")


def _ip_in_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in (_BLOCKED_ALIBABA_META, _BLOCKED_AWS_META_IPV6) or ip in _BLOCKED_CGNAT


def _ssrf_allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """运维声明的 IP 段豁免保留段拒绝——给 fake-ip TUN 代理（Clash 等，把所有域名解析到 198.18.0.0/15 或 IPv6 fdfe:dcba:9876::/64 等）的逃生口；云元数据 / CGNAT 块与 hostname 黑名单始终无条件。"""
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


def is_safe_outbound(host: str) -> tuple[bool, str]:
    """对出站目标做完整的 SSRF 校验：hostname 黑名单 + (若是 IP 字面量)保留段检查 + DNS 解析 + 全部解析结果的策略评估。

    同步函数：会执行 ``socket.getaddrinfo``。在事件循环里调用方请走
    ``anyio.to_thread.run_sync`` / ``asyncio.to_thread``，或直接使用
    ``_SafeOutboundAsyncBackend``（``safe_outbound_async_client`` / ``download_capped``）。
    """
    ok, reason = _evaluate_hostname(host)
    if not ok:
        return False, reason
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}"

    for info in infos:
        ok, reason = _evaluate_ip(info[4][0])
        if not ok:
            return False, reason
    return True, ""


def _resolve_and_validate(host: str, port: int) -> list[tuple[str, int]]:
    """同步执行 DNS 解析并对所有解析结果做 SSRF 校验；返回通过校验的 (ip, port) 列表。

    DNS 与校验都在调用方线程里跑；对 async 路径而言，调用方负责用
    ``anyio.to_thread.run_sync`` 或 ``asyncio.to_thread`` 把它移出事件循环。
    """
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
    """出站 AsyncClient 专用的 httpcore 后端：

    * DNS 解析在工作线程内完成，事件循环不会被慢解析阻塞；
    * 每个目标 IP 在 socket.connect 之前都过一次当前 SSRF 策略；
    * 实际 connect 使用已校验 IP 直连，原始 host 仍由 httpcore 用于
      HTTP Host、TLS SNI 与证书主机名校验；
    * 重定向由 httpx 自动跟随，每一跳都会重新走 ``connect_tcp``。
    """

    async def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ) -> httpcore._backends.base.AsyncNetworkStream:
        # hostname 黑名单在进 DNS 之前先判一次，省一次解析也省一次工作线程。
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
    """挂载了 ``_SafeOutboundAsyncBackend`` 的异步 httpx 传输层。

    实际建连时的目标 IP 校验由后端完成；这里保持 ``follow_redirects=False``
    与默认行为一致，避免与上层 ``download_capped`` 的逐跳校验重复发请求。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # httpcore 私有属性；连接池其余配置（limits / ssl_context）由 super 接管，不重置。
        self._pool._network_backend = _SafeOutboundAsyncBackend()


def safe_outbound_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """带建连期 SSRF 守卫的 AsyncClient 工厂。

    不再用 request hook 预检：每个 socket.connect 都会在 DNS 解析完成
    后立即校验所有目标 IP；默认 ``follow_redirects=False`` 以便上游
    ``download_capped`` 自己做逐跳校验。
    """
    transport = kwargs.pop("transport", None) or safe_outbound_async_transport()
    follow_redirects = kwargs.pop("follow_redirects", False)
    return httpx.AsyncClient(follow_redirects=follow_redirects, transport=transport, **kwargs)


def safe_outbound_async_transport() -> httpx.AsyncBaseTransport:
    """返回带 SSRF 守卫的 transport（不附带 client）——给需要在 transport 外面再包一层（如重试 / 自定义 pool）的调用方使用。"""
    return _SafeOutboundAsyncTransport()


async def download_capped(url: str, *, max_bytes: int, timeout: float = 60.0, max_redirects: int = 5) -> bytes:
    """下载远程 URL，封装大小上限、逐跳 SSRF 校验、协议白名单（{http, https}）与 HTTPS→HTTP 降级防护。

    每跳的 SSRF 校验由 ``_SafeOutboundAsyncBackend.connect_tcp`` 在 socket
    connect 之前完成（DNS 解析在工作线程里跑，不阻塞事件循环）；这里只做
    协议 / 重定向层面的额外检查。
    """
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
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                redirect_count += 1
                if redirect_count > max_redirects:
                    raise RuntimeError(f"too many redirects ({redirect_count} > {max_redirects})")

                location = resp.headers.get("location")
                if not location:
                    raise RuntimeError("redirect response missing location header")

                target_url = urljoin(current_url, location)
                target_parsed = urlparse(target_url)

                if target_parsed.scheme not in ("http", "https"):
                    raise ValueError(f"redirect to unsupported scheme: {target_parsed.scheme!r}")

                # 拒绝 HTTPS → HTTP 降级
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
