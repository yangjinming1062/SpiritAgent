import fnmatch
import ipaddress
import logging
import re
import socket
from collections.abc import Iterable
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit
from urllib.request import getproxies_environment

import anyio
import httpcore
import httpx
import idna

from spirit_agent_runner.infrastructure.constants import get_spiritagent_home

logger = logging.getLogger(__name__)

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
_METADATA_IPS = frozenset({ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200")})
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_URL_HOST = re.compile(rb"[A-Za-z0-9._:-]+")
_URL_AUTHORITY = re.compile(r"^https?://[^/?#]*", re.IGNORECASE)
_LEGACY_IPV4_HOST = re.compile(r"(?:[0-9]+|0x[0-9a-f]*)(?:\.(?:[0-9]+|0x[0-9a-f]*))*")
_DEFAULT_WEBSITE_BLOCKLIST = {"enabled": False, "domains": [], "shared_files": []}

type _IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type _IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type _ProxyBypassPattern = tuple[str, str, int | None]


class _NonPublicAddress(ValueError):
    pass


def _raw_authority_host(authority: str) -> str:
    host_port = authority.rpartition("@")[2]
    if host_port.startswith("["):
        return host_port[1:].partition("]")[0]
    return host_port.partition(":")[0]


def _encode_url_host(match: re.Match[str]) -> str:
    scheme, separator, authority = match[0].partition("://")
    credentials, at, host_port = authority.rpartition("@")
    hostname, colon, port = host_port.partition(":")
    if hostname.isascii():
        return match[0]
    try:
        mapped = idna.uts46_remap(hostname, std3_rules=False)
        labels = [
            label if label.isascii() else "xn--" + label.encode("punycode").decode("ascii")
            for label in mapped.removesuffix(".").split(".")
        ]
    except UnicodeError:
        raise ValueError("Invalid HTTP(S) hostname") from None
    hostname = ".".join(labels)
    if (
        len(hostname) > 253
        or any(not label or len(label) > 63 for label in labels)
        or ":" in hostname
        or not _URL_HOST.fullmatch(hostname.encode("ascii"))
    ):
        raise ValueError("Invalid HTTP(S) hostname")
    if mapped.endswith("."):
        hostname += "."
    return f"{scheme}{separator}{credentials}{at}{hostname}{colon}{port}"


def parse_http_url(url: str | httpx.URL) -> httpx.URL:
    if isinstance(url, str):
        url = url.strip()
        if not url.isascii():
            url = _URL_AUTHORITY.sub(_encode_url_host, url, count=1)
    try:
        target = httpx.URL(url)
    except httpx.InvalidURL:
        raise ValueError("Invalid HTTP(S) URL") from None
    if (
        target.scheme not in {"http", "https"}
        or not _URL_HOST.fullmatch(target.raw_host)
        or (target.port is not None and not 0 < target.port <= 65535)
    ):
        raise ValueError("An absolute HTTP(S) URL is required")
    if target.userinfo:
        raise ValueError("Credentials must not be embedded in URLs")
    return target


def parse_browser_url(url: str) -> tuple[httpx.URL, str]:
    target = parse_http_url(url)
    authority = f"{target.scheme}://{target.netloc.decode('ascii')}"
    # 浏览器按自己的 URL 规则处理原始路径、查询和片段。
    return target, _URL_AUTHORITY.sub(authority, url.strip(), count=1)


def _check_ip(address: _IPAddress, *, public_only: bool) -> None:
    if isinstance(address, ipaddress.IPv6Address):
        if embedded := address.ipv4_mapped or address.sixtofour:
            address = embedded
        elif address in _NAT64_NETWORK:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address in _METADATA_IPS or (isinstance(address, ipaddress.IPv4Address) and address.is_link_local):
        raise ValueError("Cloud metadata and IPv4 link-local addresses are not allowed")
    if address.is_unspecified or address.is_multicast or (address.is_reserved and not address.is_loopback):
        raise ValueError("Reserved network addresses are not allowed")
    if public_only and (not address.is_global or isinstance(address, ipaddress.IPv6Address) and address.is_site_local):
        raise _NonPublicAddress("Only public network addresses are allowed for this URL")


def _check_host(host: str, *, public_only: bool) -> _IPAddress | None:
    normalized = _normalize_host(host)
    if normalized in _BLOCKED_HOSTNAMES:
        raise ValueError("Cloud metadata and internal service hostnames are not allowed")
    if public_only and (normalized == "localhost" or normalized.endswith(".localhost")):
        raise _NonPublicAddress("Only public network addresses are allowed for this URL")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if _LEGACY_IPV4_HOST.fullmatch(normalized):
            raise ValueError("IPv4 addresses must use canonical dotted decimal notation") from None
        return None
    _check_ip(address, public_only=public_only)
    return address


def _resolve_addresses(host: str, port: int, *, public_only: bool) -> list[str]:
    if address := _check_host(host, public_only=public_only):
        return [str(address)]
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise OSError("No addresses were returned for the hostname")
    for address in addresses:
        _check_ip(ipaddress.ip_address(address), public_only=public_only)
    return addresses


def validate_browser_address(url: httpx.URL, *, allow_private: bool) -> None:
    """工具入口预检；启用内网时允许浏览器代理自行解析本机不可解析的名称。"""
    try:
        _resolve_addresses(
            url.raw_host.decode("ascii"),
            url.port or (443 if url.scheme == "https" else 80),
            public_only=not allow_private,
        )
    except _NonPublicAddress:
        raise ValueError(
            "URL targets a private or internal address (intranet access is turned off in settings)"
        ) from None
    except (OSError, UnicodeError):
        if not allow_private:
            raise


class _CheckedAsyncBackend(httpcore.AnyIOBackend):
    """建连预算覆盖 DNS 和拨号，HTTPS 保留请求原主机与证书校验。"""

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
                try:
                    addresses = await anyio.to_thread.run_sync(
                        partial(_resolve_addresses, host, port, public_only=True), abandon_on_cancel=True
                    )
                except (OSError, UnicodeError) as exc:
                    raise httpcore.ConnectError("Image server hostname could not be resolved") from exc
                errors: list[Exception] = []
                for index, address in enumerate(addresses):
                    attempt_timeout = (
                        max(0.0, deadline.deadline - anyio.current_time()) / (len(addresses) - index)
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


def _proxy_bypass_rules(entries: list[str]) -> tuple[list[_ProxyBypassPattern], list[_IPNetwork]]:
    patterns: list[_ProxyBypassPattern] = []
    networks: list[_IPNetwork] = []
    for entry in entries:
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry.strip("[]"), strict=False)
        except ValueError:
            pass
        else:
            networks.append(network)
            continue
        if "/" in entry and "://" not in entry:
            logger.warning("Ignoring an invalid NO_PROXY network")
            continue
        pattern = entry if "://" in entry else f"all://{entry if entry.startswith(('*', '[')) else '*' + entry}"
        try:
            parsed = urlsplit(pattern)
            original_host = _raw_authority_host(parsed.netloc)
            host = _decoded_idna_host(original_host)
            if host in {"", "*"} and host != original_host:
                continue
            if ":" in host:
                host = str(ipaddress.IPv6Address(host))
            patterns.append((parsed.scheme, host, parsed.port))
        except ValueError:
            logger.warning("Ignoring an invalid NO_PROXY pattern")
    return patterns, networks


def _proxy_host_matches(host: str, pattern: str) -> bool:
    if pattern in {"", "*"}:
        return True
    if pattern.startswith("*."):
        return host.endswith(pattern[1:])
    if pattern.startswith("*"):
        name = pattern[1:]
        return host == name or host.endswith(f".{name}")
    return host == pattern


class _PublicImageTransport(httpx.AsyncBaseTransport):
    def __init__(self, config: dict[str, Any] | None) -> None:
        self._config = config
        self._direct = httpx.AsyncHTTPTransport()
        self._direct._pool._network_backend = _CheckedAsyncBackend()
        self._proxies: dict[str, httpx.AsyncHTTPTransport] = {}
        proxy_env = getproxies_environment()
        entries = [entry.strip() for entry in proxy_env.get("no", "").split(",")]
        bypass_all = "*" in entries
        self._bypass_patterns, self._bypass_networks = _proxy_bypass_rules([] if bypass_all else entries)
        if not bypass_all:
            for scheme in ("http", "https", "all"):
                if proxy := proxy_env.get(scheme):
                    proxy_url = httpx.URL(proxy if "://" in proxy else f"http://{proxy}")
                    tls_context = httpx.create_ssl_context() if proxy_url.scheme == "https" else None
                    self._proxies[scheme] = httpx.AsyncHTTPTransport(
                        proxy=httpx.Proxy(proxy_url, ssl_context=tls_context)
                    )

    def _bypasses_proxy(self, url: httpx.URL, address: _IPAddress | None) -> bool:
        if address is not None:
            mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
            if any(
                address in network or (mapped is not None and mapped in network) for network in self._bypass_networks
            ):
                return True
        host = str(address) if address is not None else _decoded_idna_host(url.raw_host.decode("ascii"))
        port = url.port or (443 if url.scheme == "https" else 80)
        return any(
            scheme in {"all", url.scheme}
            and (pattern_port is None or pattern_port == port)
            and _proxy_host_matches(host, pattern)
            for scheme, pattern, pattern_port in self._bypass_patterns
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = parse_http_url(request.url)
        address = _check_host(url.raw_host.decode("ascii"), public_only=True)
        if blocked := check_website_access(str(url), config=self._config):
            raise PermissionError(blocked.message)
        proxy = self._proxies.get(url.scheme) or self._proxies.get("all")
        transport = proxy if proxy is not None and not self._bypasses_proxy(url, address) else self._direct
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        async with AsyncExitStack() as stack:
            stack.push_async_callback(self._direct.aclose)
            for transport in self._proxies.values():
                stack.push_async_callback(transport.aclose)


def create_public_image_client(*, config: dict[str, Any] | None, timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_PublicImageTransport(config), timeout=timeout, follow_redirects=True)


class WebsitePolicyError(Exception):
    pass


def _normalize_host(host: str) -> str:
    host = (host or "").strip()
    if host.isascii():
        return host.lower().rstrip(".")
    with suppress(idna.IDNAError):
        host = idna.uts46_remap(host, std3_rules=False)
    return host.rstrip(".")


def _normalize_rule(rule: Any) -> str | None:
    if not isinstance(rule, str) or not (val := rule.strip()) or val.startswith("#"):
        return None
    if "://" in val:
        parsed = urlparse(val)
        val = parsed.netloc or parsed.path
    val = _normalize_host(val.split("/", 1)[0])
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


def _load_policy_config(*, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """从内存配置读取 ``security.website_blocklist``。"""
    if not isinstance(security := (config or {}).get("security") or {}, dict):
        raise WebsitePolicyError("security must be a mapping")
    if not isinstance(website_blocklist := security.get("website_blocklist") or {}, dict):
        raise WebsitePolicyError("security.website_blocklist must be a mapping")

    return _DEFAULT_WEBSITE_BLOCKLIST | website_blocklist


def load_website_blocklist(*, config: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = _load_policy_config(config=config)

    if not isinstance(raw_domains := policy.get("domains") or [], list):
        raise WebsitePolicyError("security.website_blocklist.domains must be a list")
    if not isinstance(raw_shared_files := policy.get("shared_files") or [], list):
        raise WebsitePolicyError("security.website_blocklist.shared_files must be a list")
    if not isinstance(enabled := policy["enabled"], bool):
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

    return result


def _decoded_idna_host(host: str) -> str:
    host = _normalize_host(host)
    if "xn--" not in host:
        return host
    wildcard = "*" if host.startswith("*") else ""
    labels = host[len(wildcard) :].split(".")
    for index, label in enumerate(labels):
        if label.startswith("xn--"):
            try:
                decoded = label[4:].encode("ascii").decode("punycode")
            except UnicodeError:
                continue
            if not decoded.isascii():
                labels[index] = decoded
    return wildcard + ".".join(labels)


def _match_host_against_rule(host: str, pattern: str) -> bool:
    if not host or not pattern:
        return False
    return any(
        fnmatch.fnmatch(candidate, rule)
        if rule.startswith("*.")
        else candidate == rule or candidate.endswith(f".{rule}")
        for candidate, rule in ((host, pattern), (_decoded_idna_host(host), _decoded_idna_host(pattern)))
    )


def _extract_host_from_urlish(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc and "://" not in url:
        parsed = urlparse(f"//{url}")
    return _normalize_host(_raw_authority_host(parsed.netloc))


@dataclass
class WebsiteBlockMatch:
    url: str
    host: str
    rule: str
    source: str
    message: str


def check_website_access(url: str, *, config: dict[str, Any] | None = None) -> WebsiteBlockMatch | None:
    if not (host := _extract_host_from_urlish(url)):
        return None

    try:
        policy = load_website_blocklist(config=config)
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
                logger.info("Blocked site %s — matched rule '%s' from %s", host, pattern, source)
                return WebsiteBlockMatch(
                    url=url,
                    host=host,
                    rule=pattern,
                    source=source,
                    message=f"Blocked by website policy: '{host}' matched rule '{pattern}' from {source}",
                )
    return None
