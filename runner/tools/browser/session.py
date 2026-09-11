import atexit
import functools
import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from utils import (
    cfg_get,
    is_truthy_value,
    load_config,
)

from .camofox import is_camofox_mode
from .profile_manager import cleanup_old_profiles
from .supervisor import SUPERVISOR_REGISTRY

logger = logging.getLogger(__name__)

# 会话空闲超时（秒）
try:
    BROWSER_SESSION_INACTIVITY_TIMEOUT = max(
        int(cfg_get(load_config(), "browser", "inactivity_timeout_seconds", default=300)),
        1,
    )
except (TypeError, ValueError):
    BROWSER_SESSION_INACTIVITY_TIMEOUT = 300

DEFAULT_DIALOG_POLICY = "must_respond"
DEFAULT_DIALOG_TIMEOUT_S = 300.0
_VALID_POLICIES = frozenset({"must_respond", "auto_dismiss", "auto_accept"})


@dataclass
class SessionInfo:
    task_id: str
    cdp_url: str = ""
    profile_dir: Path | None = None
    launch_handle: object | None = None
    started_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    first_nav: bool = True
    features: dict[str, bool] = field(default_factory=dict)


_active_sessions: dict[str, SessionInfo] = {}
_last_active_session_key: dict[str, str] = {}
_LOCAL_SUFFIX = "::local"

_cleanup_lock = threading.RLock()
_cleanup_done = False
_cleanup_thread: threading.Thread | None = None
_cleanup_running = False
_cleanup_stop_event = threading.Event()


def _resolve_cdp_override(cdp_url: str) -> str:
    """把用户给的 CDP 端点规整成可连接的 URL。"""
    raw = (cdp_url or "").strip()
    if not raw:
        return ""

    lowered = raw.lower()
    if "/devtools/browser/" in lowered:
        return raw

    discovery_url = raw
    if lowered.startswith(("ws://", "wss://")):
        if raw.count(":") == 2 and raw.rstrip("/").rsplit(":", 1)[-1].isdigit() and "/" not in raw.split(":", 2)[-1]:
            discovery_url = ("http://" if lowered.startswith("ws://") else "https://") + raw.split("://", 1)[1]
        else:
            return raw

    version_url = (
        discovery_url
        if discovery_url.lower().endswith("/json/version")
        else discovery_url.rstrip("/") + "/json/version"
    )

    try:
        response = httpx.get(version_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to resolve CDP endpoint %s via %s: %s", raw, version_url, exc)
        return raw

    ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if ws_url:
        logger.info("Resolved CDP endpoint %s -> %s", raw, ws_url)
        return ws_url

    logger.warning("CDP discovery at %s did not return webSocketDebuggerUrl; using raw endpoint", version_url)
    return raw


def _get_cdp_override() -> str:
    """返回 ``config["browser"]["cdp_url"]`` 规整后的 CDP URL；无配置返回空串。"""
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if isinstance(browser_cfg, dict):
            return _resolve_cdp_override(str(browser_cfg.get("cdp_url", "") or ""))
    except Exception as e:
        logger.debug("Could not read browser.cdp_url from config: %s", e)

    return ""


def _get_dialog_policy_config() -> tuple[str, float]:
    """读取 ``browser.dialog_policy`` + ``browser.dialog_timeout_s``。"""
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if not isinstance(browser_cfg, dict):
            return DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S
        policy = str(browser_cfg.get("dialog_policy") or DEFAULT_DIALOG_POLICY)
        if policy not in _VALID_POLICIES:
            policy = DEFAULT_DIALOG_POLICY
        timeout_raw = browser_cfg.get("dialog_timeout_s")
        try:
            timeout_s = float(timeout_raw) if timeout_raw is not None else DEFAULT_DIALOG_TIMEOUT_S
            if timeout_s <= 0:
                timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        except (TypeError, ValueError):
            timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        return policy, timeout_s
    except Exception:
        return DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S


def _url_is_private(url: str) -> bool:
    """URL 主机解析到私有/LAN/loopback 地址时返回 True。

    解析失败时返回 False（让上游 SSRF 校验报错，不要静默放行）。
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip in ipaddress.ip_network("172.16.0.0/12")
                or ip in ipaddress.ip_network("100.64.0.0/10")
            )
        except ValueError:
            pass

        if hostname in {"localhost"} or hostname.endswith(".localhost"):
            return True
        if hostname.endswith(".local") or hostname.endswith(".lan") or hostname.endswith(".internal"):
            return True
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return False
        for _, _, _, _, sockaddr in addr_info:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
                return True
        return False
    except Exception as exc:
        logger.debug("URL-privacy check failed for %s: %s", url, exc)
        return False


def _navigation_session_key(task_id: str, url: str) -> str:
    """override > Camofox > 公网 URL 用 task_id；私有 URL 走 ``::local`` sidecar。"""
    if task_id is None:
        task_id = "default"
    if _get_cdp_override():
        return task_id
    if is_camofox_mode():
        return task_id
    if not _url_is_private(url):
        return task_id
    return f"{task_id}{_LOCAL_SUFFIX}"


def _last_session_key(task_id: str) -> str:
    """非 nav 工具默认复用上次活跃 session 的 key。"""
    if task_id is None:
        task_id = "default"
    return _last_active_session_key.get(task_id, task_id)


def reset_session_caches() -> None:
    """清掉 ``_allow_private_urls`` 的 lru 缓存 — config.update 后由 server 调用, 让设置即时生效。"""
    _allow_private_urls.cache_clear()


@functools.lru_cache(maxsize=1)
def _allow_private_urls() -> bool:
    """读取 ``config["browser"]["allow_private_urls"]``。"""
    try:
        val = cfg_get(load_config(), "browser", "allow_private_urls")
        return is_truthy_value(val, default=False)
    except Exception as e:
        logger.debug("Could not read allow_private_urls from config: %s", e)
        return False


def get_or_create_session(task_id: str) -> SessionInfo:
    with _cleanup_lock:
        info = _active_sessions.get(task_id)
        if info is None:
            info = SessionInfo(task_id=task_id)
            _active_sessions[task_id] = info
        info.last_active_at = time.time()
        _start_browser_cleanup_thread()
        return info


def touch_session(task_id: str) -> None:
    with _cleanup_lock:
        info = _active_sessions.get(task_id)
        if info is not None:
            info.last_active_at = time.time()


def close_session(task_id: str) -> None:
    with _cleanup_lock:
        info = _active_sessions.pop(task_id, None)
        for k, v in list(_last_active_session_key.items()):
            if k == task_id or v == task_id:
                _last_active_session_key.pop(k, None)

    if info is not None:
        try:
            SUPERVISOR_REGISTRY.stop(task_id)
        except Exception as e:
            logger.debug("Error stopping supervisor for %s: %s", task_id, e)
        if info.launch_handle is not None:
            try:
                info.launch_handle.terminate()
            except Exception as e:
                logger.debug("Error terminating native browser for %s: %s", task_id, e)


def cleanup_all_browsers() -> None:
    """关闭所有活跃浏览器会话与主管。"""
    with _cleanup_lock:
        sessions = list(_active_sessions.items())
        _active_sessions.clear()
        _last_active_session_key.clear()

    for task_id, info in sessions:
        try:
            SUPERVISOR_REGISTRY.stop(task_id)
        except Exception as e:
            logger.debug("Error stopping supervisor for %s: %s", task_id, e)
        if info.launch_handle is not None:
            try:
                info.launch_handle.terminate()
            except Exception as e:
                logger.debug("Error terminating native browser for %s: %s", task_id, e)

    try:
        cleanup_old_profiles()
    except Exception as e:
        logger.debug("Error cleaning old profiles: %s", e)


def _cleanup_inactive_browser_sessions() -> None:
    current_time = time.time()
    to_cleanup = []
    with _cleanup_lock:
        for task_id, info in _active_sessions.items():
            if current_time - info.last_active_at > BROWSER_SESSION_INACTIVITY_TIMEOUT:
                to_cleanup.append(task_id)

    for task_id in to_cleanup:
        logger.info("Closing inactive browser session: %s", task_id)
        close_session(task_id)


def _browser_cleanup_worker() -> None:
    global _cleanup_running
    while _cleanup_running:
        if _cleanup_stop_event.wait(timeout=30.0):
            break
        if not _cleanup_running:
            break
        try:
            _cleanup_inactive_browser_sessions()
        except Exception as e:
            logger.debug("Error in browser cleanup worker: %s", e)


def _start_browser_cleanup_thread() -> None:
    global _cleanup_thread, _cleanup_running
    with _cleanup_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_running = True
            _cleanup_stop_event.clear()
            _cleanup_thread = threading.Thread(
                target=_browser_cleanup_worker,
                name="browser-session-cleanup",
                daemon=True,
            )
            _cleanup_thread.start()


def _stop_browser_cleanup_thread() -> None:
    global _cleanup_running
    _cleanup_running = False
    _cleanup_stop_event.set()


def _emergency_cleanup_all_sessions() -> None:
    """进程退出时的兜底清理。"""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    cleanup_all_browsers()


# atexit LIFO：先注册的 last 执行。先注册 stop 再注册 emergency，
# 这样进程退出时 emergency 先清理完会话，再让清理线程收尾。
atexit.register(_stop_browser_cleanup_thread)
atexit.register(_emergency_cleanup_all_sessions)
