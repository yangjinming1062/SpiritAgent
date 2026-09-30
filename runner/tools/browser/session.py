import atexit
import logging
import threading
import time
from dataclasses import dataclass, field

import httpx
from utils import (
    cfg_get,
    is_truthy_value,
    load_config,
)

from .dialog_manager import _VALID_POLICIES, DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S
from .engine.launcher import NativeBrowserProcess
from .profile_manager import cleanup_old_profiles
from .supervisor import SUPERVISOR_REGISTRY

logger = logging.getLogger(__name__)

_DEFAULT_INACTIVITY_TIMEOUT_S = 300


@dataclass
class SessionInfo:
    launch_handle: NativeBrowserProcess | None = None
    last_active_at: float = field(default_factory=time.time)


_active_sessions: dict[str, SessionInfo] = {}

_cleanup_lock = threading.RLock()
_cleanup_done = False
_cleanup_thread: threading.Thread | None = None
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
    """返回 ``config["browser"]["cdp_url"]`` 规整后的 CDP URL；无配置返回空串。会对端点发起发现请求。"""
    return _resolve_cdp_override(str(cfg_get(load_config(), "browser", "cdp_url", default="")))


def _get_dialog_policy_config() -> tuple[str, float]:
    """读取 ``browser.dialog_policy`` + ``browser.dialog_timeout_s``，非法值回落默认。"""
    policy = str(cfg_get(load_config(), "browser", "dialog_policy", default=DEFAULT_DIALOG_POLICY))
    if policy not in _VALID_POLICIES:
        policy = DEFAULT_DIALOG_POLICY
    try:
        timeout_s = float(cfg_get(load_config(), "browser", "dialog_timeout_s", default=DEFAULT_DIALOG_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout_s = DEFAULT_DIALOG_TIMEOUT_S
    return policy, timeout_s if timeout_s > 0 else DEFAULT_DIALOG_TIMEOUT_S


def _inactivity_timeout_s() -> int:
    """会话空闲超时（秒）；配置由 Client 在启动后推送，须在使用时读取。"""
    raw = cfg_get(load_config(), "browser", "inactivity_timeout_seconds", default=_DEFAULT_INACTIVITY_TIMEOUT_S)
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return _DEFAULT_INACTIVITY_TIMEOUT_S


def _allow_private_urls() -> bool:
    """读取 ``config["browser"]["allow_private_urls"]``。"""
    return is_truthy_value(cfg_get(load_config(), "browser", "allow_private_urls"), default=False)


def get_or_create_session(task_id: str) -> SessionInfo:
    with _cleanup_lock:
        info = _active_sessions.get(task_id)
        if info is None:
            info = SessionInfo()
            _active_sessions[task_id] = info
        info.last_active_at = time.time()
        _start_browser_cleanup_thread()
        return info


def touch_session(task_id: str) -> None:
    with _cleanup_lock:
        info = _active_sessions.get(task_id)
        if info is not None:
            info.last_active_at = time.time()


def _shutdown_session(task_id: str, info: SessionInfo) -> None:
    try:
        SUPERVISOR_REGISTRY.stop(task_id)
    except Exception as e:
        logger.debug("Error stopping supervisor for %s: %s", task_id, e)
    if info.launch_handle is not None:
        info.launch_handle.terminate()


def cleanup_all_browsers() -> None:
    """关闭所有活跃浏览器会话与主管。"""
    with _cleanup_lock:
        sessions = list(_active_sessions.items())
        _active_sessions.clear()

    for task_id, info in sessions:
        _shutdown_session(task_id, info)

    try:
        cleanup_old_profiles()
    except Exception as e:
        logger.debug("Error cleaning old profiles: %s", e)


def _cleanup_inactive_browser_sessions() -> None:
    cutoff = time.time() - _inactivity_timeout_s()
    with _cleanup_lock:
        expired = [(task_id, info) for task_id, info in _active_sessions.items() if info.last_active_at < cutoff]
        for task_id, _ in expired:
            del _active_sessions[task_id]

    for task_id, info in expired:
        logger.info("Closing inactive browser session: %s", task_id)
        _shutdown_session(task_id, info)


def _browser_cleanup_worker() -> None:
    while not _cleanup_stop_event.wait(timeout=30.0):
        try:
            _cleanup_inactive_browser_sessions()
        except Exception as e:
            logger.warning("Error in browser cleanup worker: %s", e)


def _start_browser_cleanup_thread() -> None:
    global _cleanup_thread
    with _cleanup_lock:
        if _cleanup_thread is None or not _cleanup_thread.is_alive():
            _cleanup_stop_event.clear()
            _cleanup_thread = threading.Thread(
                target=_browser_cleanup_worker,
                name="browser-session-cleanup",
                daemon=True,
            )
            _cleanup_thread.start()


def _stop_browser_cleanup_thread() -> None:
    _cleanup_stop_event.set()


def _emergency_cleanup_all_sessions() -> None:
    """进程退出时的兜底清理。"""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    cleanup_all_browsers()


# atexit LIFO：先注册 stop，exit 时 emergency 先清会话。
atexit.register(_stop_browser_cleanup_thread)
atexit.register(_emergency_cleanup_all_sessions)
