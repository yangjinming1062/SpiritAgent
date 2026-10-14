import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from spirit_agent_runner.contracts.config import cfg_get, is_truthy_value, load_config
from spirit_agent_runner.features.browser.camofox_state import CamofoxState
from spirit_agent_runner.features.browser.dialog_manager import (
    DEFAULT_DIALOG_POLICY,
    DEFAULT_DIALOG_TIMEOUT_S,
    VALID_POLICIES,
)
from spirit_agent_runner.features.browser.engine.launcher import NativeBrowserProcess, launch_chromium
from spirit_agent_runner.features.browser.profile_manager import cleanup_old_profiles, profile_root, resolve_profile_dir
from spirit_agent_runner.features.browser.supervisor import CDPSupervisor
from spirit_agent_runner.features.browser.supervisor_registry import SupervisorRegistry

logger = logging.getLogger(__name__)
_DEFAULT_INACTIVITY_TIMEOUT_S = 300


@dataclass
class SessionInfo:
    launch_handle: NativeBrowserProcess | None = None
    supervisor: CDPSupervisor | None = None
    active_users: int = 0
    last_active_at: float = field(default_factory=time.time)


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
        parts = urlsplit(raw)
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            return raw
        discovery_url = urlunsplit(("http" if parts.scheme == "ws" else "https", parts.netloc, "", "", ""))

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


def get_cdp_override() -> str:
    """返回 ``config["browser"]["cdp_url"]`` 规整后的 CDP URL；无配置返回空串。会对端点发起发现请求。"""
    return _resolve_cdp_override(str(cfg_get(load_config(), "browser", "cdp_url", default="")))


def get_dialog_policy_config() -> tuple[str, float]:
    """读取 ``browser.dialog_policy`` + ``browser.dialog_timeout_s``，非法值回落默认。"""
    policy = str(cfg_get(load_config(), "browser", "dialog_policy", default=DEFAULT_DIALOG_POLICY))
    if policy not in VALID_POLICIES:
        policy = DEFAULT_DIALOG_POLICY
    try:
        timeout_s = float(cfg_get(load_config(), "browser", "dialog_timeout_s", default=DEFAULT_DIALOG_TIMEOUT_S))
    except (TypeError, ValueError):
        timeout_s = DEFAULT_DIALOG_TIMEOUT_S
    return policy, timeout_s if timeout_s > 0 else DEFAULT_DIALOG_TIMEOUT_S


def inactivity_timeout_s(config: dict[str, Any]) -> int:
    """会话空闲超时（秒）；配置由 Client 在启动后推送，须在使用时读取。"""
    raw = cfg_get(config, "browser", "inactivity_timeout_seconds", default=_DEFAULT_INACTIVITY_TIMEOUT_S)
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return _DEFAULT_INACTIVITY_TIMEOUT_S


def allow_private_urls() -> bool:
    """读取 ``config["browser"]["allow_private_urls"]``。"""
    return is_truthy_value(cfg_get(load_config(), "browser", "allow_private_urls"), default=False)


class BrowserManager:
    """Own task sessions, supervisor lifecycle locks and idle cleanup for one Runner."""

    def __init__(self, config_provider: Callable[[], dict[str, Any]] = load_config) -> None:
        self._config_provider = config_provider
        self._registry = SupervisorRegistry()
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.camofox = CamofoxState()

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Browser manager is closed")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._cleanup_worker,
                    name="browser-session-cleanup",
                    daemon=True,
                )
                self._thread.start()

    @contextmanager
    def session(self, task_id: str, *, create: bool = False) -> Iterator[SessionInfo | None]:
        with self._registry.lifecycle(task_id), self._lock:
            if self._closed:
                raise RuntimeError("Browser manager is closed")
            info = self._sessions.get(task_id)
            if info is None and create:
                info = SessionInfo()
                self._sessions[task_id] = info
            if info is not None:
                info.active_users += 1
                info.last_active_at = time.time()
        try:
            yield info
        finally:
            if info is not None:
                with self._registry.lifecycle(task_id), self._lock:
                    info.active_users -= 1
                    info.last_active_at = time.time()

    def ensure_supervisor(self, task_id: str) -> CDPSupervisor:
        with self._registry.lifecycle(task_id):
            with self._lock:
                if self._closed:
                    raise RuntimeError("Browser manager is closed")
                info = self._sessions[task_id]
            supervisor = self._registry.get(task_id)
            if supervisor is not None and supervisor.active:
                try:
                    supervisor.pages.ensure_active_page()
                except RuntimeError:
                    # Closing the last headed window may drop CDP during page recovery.
                    if supervisor.active and (info.launch_handle is None or info.launch_handle.poll() is None):
                        raise
                else:
                    info.supervisor = supervisor
                    return supervisor
            if supervisor is not None and info.active_users > 1:
                raise RuntimeError(
                    "Browser connection is recovering while other tool calls are still using this session",
                )
            self._registry.stop(task_id)
            info.supervisor = None
            if info.launch_handle is not None:
                info.launch_handle.terminate()
                info.launch_handle = None
            try:
                if not (cdp_url := get_cdp_override()):
                    info.launch_handle = launch_chromium(profile_dir=resolve_profile_dir(task_id))
                    cdp_url = info.launch_handle.cdp_url
                policy, timeout_s = get_dialog_policy_config()
                supervisor = self._registry.get_or_start(
                    task_id,
                    cdp_url,
                    launch_handle=info.launch_handle,
                    auto_owned=info.launch_handle is not None,
                    dialog_policy=policy,
                    dialog_timeout_s=timeout_s,
                )
            except BaseException:
                if info.launch_handle is not None:
                    info.launch_handle.terminate()
                    info.launch_handle = None
                raise
            info.supervisor = supervisor
            return supervisor

    def _shutdown_session(self, task_id: str, info: SessionInfo) -> None:
        try:
            if info.supervisor is not None:
                self._registry.stop(task_id, expected=info.supervisor)
        finally:
            info.supervisor = None
            if info.launch_handle is not None:
                info.launch_handle.terminate()
                info.launch_handle = None

    def cleanup_idle(self) -> None:
        cutoff = time.time() - inactivity_timeout_s(self._config_provider())
        with self._lock:
            expired = [
                (key, info)
                for key, info in self._sessions.items()
                if info.active_users == 0 and info.last_active_at < cutoff
            ]
        for task_id, info in expired:
            with self._registry.lifecycle(task_id):
                with self._lock:
                    if self._sessions.get(task_id) is not info or info.active_users or info.last_active_at >= cutoff:
                        continue
                    del self._sessions[task_id]
                logger.info("Closing inactive browser session: %s", task_id)
                self._shutdown_session(task_id, info)

    def _cleanup_worker(self) -> None:
        while not self._stop.wait(30.0):
            try:
                self.cleanup_idle()
            except Exception:
                logger.exception("Browser idle cleanup failed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            sessions = list(self._sessions.items())
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        for task_id, info in sessions:
            with self._registry.lifecycle(task_id):
                with self._lock:
                    if self._sessions.get(task_id) is not info:
                        continue
                    del self._sessions[task_id]
                try:
                    self._shutdown_session(task_id, info)
                except Exception:
                    logger.exception("Browser session shutdown failed for %s", task_id)
        self.camofox.close()
        try:
            cleanup_old_profiles(root=profile_root(self._config_provider()))
        except Exception:
            logger.exception("Browser profile cleanup failed")
