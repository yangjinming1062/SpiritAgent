import asyncio
import fnmatch
import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import anyio
import httpcore
import httpx

from .config import is_truthy_value, load_config
from .constants import get_spiritagent_home

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})

_ALWAYS_BLOCKED_IPS = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",
        "169.254.170.2",
        "169.254.169.253",
        "fd00:ec2::254",
        "100.100.100.200",
        "::ffff:169.254.169.254",
        "::ffff:169.254.170.2",
        "::ffff:169.254.169.253",
        "::ffff:100.100.100.200",
    )
)

_ALWAYS_BLOCKED_NETWORKS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("::ffff:169.254.0.0/112"))

_TRUSTED_PRIVATE_IP_HOSTS = frozenset({"multimedia.nt.qq.com.cn"})
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

_DEFAULT_WEBSITE_BLOCKLIST = {"enabled": False, "domains": [], "shared_files": []}

_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached_policy: dict[str, Any] | None = None
_cached_policy_time: float = 0.0


def normalize_url_for_request(url: str) -> str:
    if not isinstance(url, str) or not (raw := url.strip()):
        return url
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if parsed.scheme.lower() not in {"http", "https"}:
        return raw
    netloc = parsed.netloc
    if hostname := parsed.hostname:
        try:
            ascii_host = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = hostname
        if ascii_host != hostname:
            netloc = netloc.replace(hostname, ascii_host, 1)
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            quote(parsed.path, safe="/%:@!$&'()*+,;="),
            quote(parsed.query, safe="/%:@!$&'()*+,;=?"),
            quote(parsed.fragment, safe="/%:@!$&'()*+,;=?"),
        ),
    )


def _global_allow_private_urls() -> bool:
    """仅读取 ``security.allow_private_urls`` (跨工具的 SSRF 全局闸门).

    历史原因曾把 ``browser.allow_private_urls`` 也 OR 进来 — 但 ``browser.*`` 是开发者本地调试 localhost 的逃生口,
    让它顺带关掉 vision_analyze / webhook 等所有 HTTP 工具的 SSRF 是危险的设计耦合。现在两者作用域独立:
    本函数专门管全局 HTTP 闸门, 浏览器本地访问由 ``_allow_private_urls()`` 在 ``browser/session.py`` 单独判读。
    """
    try:
        cfg = load_config()
        security = cfg.get("security")
        return isinstance(security, dict) and is_truthy_value(security.get("allow_private_urls"), default=False)
    except Exception:
        return False


def _is_always_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # 把所有 IPv4-in-IPv6 变体映射成 IPv4 再判定; Python 默认只识别 ``::ffff:a.b.c.d`` 一种。
        # NAT64 (``64:ff9b::/96``)、6to4 (``2002::/16``)、deprecated IPv4-compat (``::a.b.c.d``) 都不映射,
        # 会让 ``169.254.169.254`` 通过 ``64:ff9b::169.254.169.254`` 绕开元数据端点拦截。
        if (mapped := ip.ipv4_mapped) is not None:
            ip = mapped
        elif ip.sixtofour:  # 2002:WWXX:YYZZ:... -> a.b.c.d
            ip = ip.sixtofour
        elif (nat64_prefix := ipaddress.IPv6Network("64:ff9b::/96", False)) and ip in nat64_prefix:
            tail = int(ip) & 0xFFFFFFFF
            ip = ipaddress.IPv4Address(tail)
        else:
            # deprecated ``::a.b.c.d`` IPv4-compatible IPv6 address (RFC 4291 §2.5.5.1)
            try:
                if ip.ipv4_compat is not None:
                    ip = ip.ipv4_compat  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in _CGNAT_NETWORK
    )


def is_always_blocked_url(url: str) -> bool:
    try:
        if not (hostname := (urlparse(url).hostname or "").strip().lower().rstrip(".")):
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname (always-blocked floor): %s", hostname)
            return True
        try:
            if _is_always_blocked(ipaddress.ip_address(hostname)):
                logger.warning("Blocked request to cloud metadata address (always-blocked floor): %s", hostname)
                return True
            return False
        except ValueError:
            pass
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        for _, _, _, _, sockaddr in addr_info:
            try:
                resolved = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if _is_always_blocked(resolved):
                logger.warning(
                    "Blocked request to cloud metadata address (always-blocked floor): %s -> %s",
                    hostname,
                    sockaddr[0],
                )
                return True
        return False
    except Exception as exc:
        logger.debug("is_always_blocked_url error for %s: %s", url, exc)
        return False


def _allows_private_ip_resolution(hostname: str, scheme: str) -> bool:
    return scheme == "https" and hostname in _TRUSTED_PRIVATE_IP_HOSTS


def verify_ip_not_blocked(
    ip: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
    hostname: str = "",
    scheme: str = "https",
) -> None:
    """校验单个 IP 是否违反 SSRF 防护策略；违规时抛出 ValueError。"""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip.strip("[]"))
    if _is_always_blocked(ip):
        raise ValueError(f"Blocked request to cloud metadata address: {ip}")
    allow_all_private = _global_allow_private_urls()
    allow_private_ip = bool(hostname and _allows_private_ip_resolution(hostname, scheme))
    if not allow_all_private and not allow_private_ip and _is_blocked_ip(ip):
        raise ValueError(f"Blocked request to private/internal address: {ip}")


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"}:
            logger.warning("Blocked request — unsupported URL scheme: %s", scheme or "<empty>")
            return False
        if not hostname:
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for _, _, _, _, sockaddr in addr_info:
            try:
                verify_ip_not_blocked(sockaddr[0], hostname=hostname, scheme=scheme)
            except ValueError as e:
                logger.warning("%s: %s -> %s", e, hostname, sockaddr[0])
                return False

        if _global_allow_private_urls():
            logger.debug("Allowing private/internal resolution (security.allow_private_urls=true): %s", hostname)
        elif _allows_private_ip_resolution(hostname, scheme):
            logger.debug("Allowing trusted hostname despite private/internal resolution: %s", hostname)
        return True
    except Exception as exc:
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False


async def async_is_safe_url(url: str) -> bool:
    return await asyncio.to_thread(is_safe_url, url)


def _resolve_and_validate(host: str, port: int, *, scheme: str) -> list[tuple]:
    """同步执行 DNS 解析并校验所有解析结果；返回通过校验的 sockaddr 列表。

    返回列表只含通过 ``verify_ip_not_blocked`` 的 IP；上层 ``socket.connect``
    必须从该列表里取目标 IP，否则再次 DNS 解析会重新打开 rebinding 窗口。
    """
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"DNS resolution failed for {host}: {exc}") from exc
    if not addr_info:
        raise httpcore.ConnectError(f"No addresses returned for {host}")

    validated: list[tuple] = []
    for family, socktype, proto, canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            verify_ip_not_blocked(ip_str, hostname=host, scheme=scheme)
        except ValueError as exc:
            logger.warning("%s: %s -> %s", exc, host, ip_str)
            raise httpcore.ConnectError(f"SSRF guard: {exc}") from exc
        validated.append((family, socktype, proto, canon, sockaddr))
    return validated


def _scheme_for_port(port: int) -> str:
    return "https" if port == 443 else "http"


def _connect_with_validated_ip(
    addr_info: list[tuple],
    *,
    timeout: float | None,
    local_address: str | None,
    socket_options: Iterable[tuple] | None,
) -> socket.socket:
    """按顺序尝试用已校验 IP 直接 connect，保留原始 Host / TLS SNI / 证书主机名校验。"""
    source_address = None if local_address is None else (local_address, 0)
    socket_options = list(socket_options or [])
    last_error: OSError | None = None
    for family, socktype, proto, _canon, sockaddr in addr_info:
        sock = socket.socket(family, socktype, proto)
        try:
            for option in socket_options:
                sock.setsockopt(*option)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if timeout is not None:
                sock.settimeout(timeout)
            if source_address is not None:
                sock.bind(source_address)
            sock.connect(sockaddr[:2])
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
            continue
    if last_error is not None:
        raise last_error
    raise httpcore.ConnectError("No validated addresses available for connect")


# ---------------------------------------------------------------------------
# httpcore NetworkBackend 实现：在 socket.connect 之前再次解析并校验目标 IP，
# 直接使用已校验 IP 建连，原始 Host / TLS SNI / 证书主机名校验保持不变。
# ---------------------------------------------------------------------------


class _SafeSyncBackend(httpcore._backends.sync.SyncBackend):
    """同步 httpcore 后端：connect_tcp 阶段强制重新解析并校验每一个目标 IP。

    父类 ``socket.create_connection`` 会再次调 getaddrinfo，从而引入 DNS
    rebinding TOCTOU 窗口；此处改为我们显式解析 + 校验 + 直连 IP，
    原始 hostname 仍由 httpcore 用于 HTTP Host、TLS SNI 与证书校验。
    """

    def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ):
        from httpcore._backends.sync import SyncStream

        # hostname 黑名单在 DNS 之前先拦截，省一次解析。
        if _normalize_host(host) in _BLOCKED_HOSTNAMES:
            raise httpcore.ConnectError(f"Blocked request to internal hostname: {host}")

        addr_info = _resolve_and_validate(host, port, scheme=_scheme_for_port(port))
        sock = _connect_with_validated_ip(
            addr_info,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return SyncStream(sock)

    # connect_unix_socket: 不参与 TCP 校验，保持父类语义不变。
    def connect_unix_socket(  # type: ignore[override]
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[tuple] | None = None,
    ):
        return super().connect_unix_socket(path, timeout=timeout, socket_options=socket_options)


class _SafeAsyncBackend(httpcore._backends.auto.AutoBackend):
    """异步 httpcore 后端：connect_tcp 阶段强制重新解析并校验每一个目标 IP。

    解析与 socket.create_connection 在工作线程里执行以避免阻塞事件循环；
    实际 connect 走 inner backend 的 anyio/trio connect_tcp，传入已校验 IP，
    原始 hostname 仍由 httpcore 用于 HTTP Host、TLS SNI 与证书校验。
    """

    async def connect_tcp(  # type: ignore[override]
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ):
        # hostname 黑名单在 DNS 之前先拦截，省一次解析也省一次工作线程。
        if _normalize_host(host) in _BLOCKED_HOSTNAMES:
            raise httpcore.ConnectError(f"Blocked request to internal hostname: {host}")

        addr_info = await anyio.to_thread.run_sync(
            partial(_resolve_and_validate, host, port, scheme=_scheme_for_port(port)),
        )

        await self._init_backend()
        last_error: Exception | None = None
        for _family, _socktype, _proto, _canon, sockaddr in addr_info:
            try:
                return await self._backend.connect_tcp(
                    sockaddr[0],
                    port,
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
    ):
        await self._init_backend()
        return await self._backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)


def _swap_pool_backend(pool: Any, backend: Any) -> None:
    """替换 httpcore ConnectionPool 的 NetworkBackend；保持现有连接池其余配置不变。"""
    pool._network_backend = backend  # noqa: SLF001 - httpcore 没有公开的 setter


class SafeHTTPTransport(httpx.HTTPTransport):
    """同步 HTTP 传输层：

    * handle_request 在请求前对 URL 字符串做 SSRF 预检（快速失败）；
    * 实际 socket.connect 由 ``_SafeSyncBackend`` 在建连时再次解析并校验
      所有目标 IP，杜绝 DNS rebinding 窗口；
    * 原始 Host / TLS SNI / 证书主机名校验保持 httpx 默认行为。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _swap_pool_backend(self._pool, _SafeSyncBackend())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if not is_safe_url(url_str):
            raise ValueError(f"SSRF guard blocked request to unsafe URL: {url_str}")
        return super().handle_request(request)


class SafeAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """异步 HTTP 传输层：

    * handle_async_request 在请求前对 URL 字符串做 SSRF 预检（快速失败）；
    * 实际 socket.connect 由 ``_SafeAsyncBackend`` 在建连时再次解析并校验
      所有目标 IP，并把 DNS 解析移出事件循环；
    * 重定向由 httpx 自动处理，每一跳都会重新调用 ``_SafeAsyncBackend``。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _swap_pool_backend(self._pool, _SafeAsyncBackend())

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if not await async_is_safe_url(url_str):
            raise ValueError(f"SSRF guard blocked request to unsafe URL: {url_str}")
        return await super().handle_async_request(request)


def create_safe_client(**kwargs: Any) -> httpx.Client:
    """创建挂载了 ``SafeHTTPTransport`` 的同步 ``httpx.Client``。"""
    if "transport" not in kwargs:
        kwargs["transport"] = SafeHTTPTransport()
    return httpx.Client(**kwargs)


def create_safe_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """创建挂载了 ``SafeAsyncHTTPTransport`` 的异步 ``httpx.AsyncClient``。"""
    if "transport" not in kwargs:
        kwargs["transport"] = SafeAsyncHTTPTransport()
    return httpx.AsyncClient(**kwargs)


def check_redirect_url_safety(original_url: str, redirect_url: str) -> bool:
    """校验重定向目标 URL 是否命中云元数据黑名单或 SSRF 防护。"""
    if not redirect_url or not redirect_url.startswith(("http://", "https://")):
        return True
    if is_always_blocked_url(redirect_url):
        logger.warning("Blocked redirect to cloud metadata address: %s -> %s", original_url, redirect_url)
        return False
    if not is_safe_url(redirect_url):
        logger.warning("Blocked redirect to unsafe target: %s -> %s", original_url, redirect_url)
        return False
    return True


class WebsitePolicyError(Exception):
    pass


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def _normalize_rule(rule: Any) -> str | None:
    if not isinstance(rule, str) or not (val := rule.strip().lower()) or val.startswith("#"):
        return None
    if "://" in val:
        parsed = urlparse(val)
        val = parsed.netloc or parsed.path
    val = val.split("/", 1)[0].strip().rstrip(".")
    return (val[4:] if val.startswith("www.") else val) or None


def _iter_blocklist_file_rules(path: Path) -> list[str]:
    try:
        return [
            norm
            for line in path.read_text(encoding="utf-8").splitlines()
            if (stripped := line.strip()) and not stripped.startswith("#") and (norm := _normalize_rule(stripped))
        ]
    except FileNotFoundError:
        logger.warning("Shared blocklist file not found (skipping): %s", path)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read shared blocklist file %s (skipping): %s", path, exc)
    return []


def _load_policy_config() -> dict[str, Any]:
    """从内存配置读取 ``security.website_blocklist``。"""
    config = load_config()
    if not isinstance(config, dict):
        raise WebsitePolicyError("config root must be a mapping")
    if not isinstance(security := config.get("security") or {}, dict):
        raise WebsitePolicyError("security must be a mapping")
    if not isinstance(website_blocklist := security.get("website_blocklist") or {}, dict):
        raise WebsitePolicyError("security.website_blocklist must be a mapping")

    return _DEFAULT_WEBSITE_BLOCKLIST | website_blocklist


def load_website_blocklist() -> dict[str, Any]:
    global _cached_policy, _cached_policy_time

    now = time.monotonic()
    with _cache_lock:
        if _cached_policy is not None and (now - _cached_policy_time) < _CACHE_TTL_SECONDS:
            return _cached_policy

    policy = _load_policy_config()

    if not isinstance(raw_domains := policy.get("domains") or [], list):
        raise WebsitePolicyError("security.website_blocklist.domains must be a list")
    if not isinstance(raw_shared_files := policy.get("shared_files") or [], list):
        raise WebsitePolicyError("security.website_blocklist.shared_files must be a list")
    if not isinstance(enabled := policy.get("enabled", True), bool):
        raise WebsitePolicyError("security.website_blocklist.enabled must be a boolean")

    rules: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_rule in raw_domains:
        if (normalized := _normalize_rule(raw_rule)) and ("config", normalized) not in seen:
            rules.append({"pattern": normalized, "source": "config"})
            seen.add(("config", normalized))

    for shared_file in raw_shared_files:
        if isinstance(shared_file, str) and shared_file.strip():
            path = Path(shared_file).expanduser()
            resolved = path if path.is_absolute() else (get_spiritagent_home() / path).resolve()
            for normalized in _iter_blocklist_file_rules(resolved):
                if (key := (str(resolved), normalized)) not in seen:
                    rules.append({"pattern": normalized, "source": str(resolved)})
                    seen.add(key)

    result = {"enabled": enabled, "rules": rules}

    with _cache_lock:
        _cached_policy, _cached_policy_time = result, now

    return result


def _match_host_against_rule(host: str, pattern: str) -> bool:
    if not host or not pattern:
        return False
    return (
        fnmatch.fnmatch(host, pattern)
        if pattern.startswith("*.")
        else (host == pattern or host.endswith(f".{pattern}"))
    )


def _extract_host_from_urlish(url: str) -> str:
    parsed = urlparse(url)
    if host := _normalize_host(parsed.hostname or parsed.netloc):
        return host
    return _normalize_host(s.hostname or s.netloc) if "://" not in url and (s := urlparse(f"//{url}")) else ""


@dataclass
class WebsiteBlockMatch:
    url: str
    host: str
    rule: str
    source: str
    message: str


def check_website_access(url: str) -> WebsiteBlockMatch | None:
    with _cache_lock:
        if _cached_policy is not None and not _cached_policy.get("enabled"):
            return None

    if not (host := _extract_host_from_urlish(url)):
        return None

    try:
        policy = load_website_blocklist()
    except WebsitePolicyError as exc:
        logger.warning("Website policy config error (failing open): %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error loading website policy (failing open): %s", exc)
        return None

    if policy.get("enabled"):
        for rule in policy.get("rules", []):
            if _match_host_against_rule(host, pattern := rule.get("pattern", "")):
                source = rule.get("source", "config")
                logger.info("Blocked URL %s — matched rule '%s' from %s", url, pattern, source)
                return WebsiteBlockMatch(
                    url=url,
                    host=host,
                    rule=pattern,
                    source=source,
                    message=f"Blocked by website policy: '{host}' matched rule '{pattern}' from {source}",
                )
    return None
