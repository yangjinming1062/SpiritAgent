import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from spirit_agent_runner.contracts.config import cfg_get, is_truthy_value, load_config
from spirit_agent_runner.contracts.context import current_context
from spirit_agent_runner.contracts.tooling import ToolContext
from spirit_agent_runner.features.browser.camofox_state import CamofoxState
from spirit_agent_runner.features.browser.dialog_manager import (
    DEFAULT_DIALOG_POLICY,
    DEFAULT_DIALOG_TIMEOUT_S,
    VALID_POLICIES,
)
from spirit_agent_runner.features.browser.engine.launcher import NativeBrowserProcess, launch_chromium
from spirit_agent_runner.features.browser.profile_manager import cleanup_old_profiles, profile_root
from spirit_agent_runner.features.browser.supervisor import CDPSupervisor

logger = logging.getLogger(__name__)
_DEFAULT_INACTIVITY_TIMEOUT_S = 300


@dataclass
class SessionInfo:
    target: str | Path
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
    """管理唯一浏览器会话、CDP 主管和自有进程，串行启停与空闲回收。"""

    def __init__(self, current_config: Callable[[], ToolContext]) -> None:
        self._current_config = current_config
        self._lifecycle = threading.Lock()
        self._session: SessionInfo | None = None
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
                    target=self._cleanup_worker, name="browser-session-cleanup", daemon=True
                )
                self._thread.start()

    @contextmanager
    def session(self, *, create: bool = False) -> Iterator[SessionInfo | None]:
        call = current_context()
        target = (
            str(cfg_get(call.config, "browser", "cdp_url", default="")).strip() or profile_root(call.config) / "default"
        )
        with self._lifecycle, self._lock:
            if self._closed:
                raise RuntimeError("Browser manager is closed")
            info = self._session
            if create:
                current = self._current_config()
                replacing = info is not None and info.target != target
                if call.config_generation != current.config_generation and (
                    replacing or call.config.get("browser") != current.config.get("browser")
                ):
                    raise RuntimeError("Browser configuration changed; retry navigation with the current configuration")
            if info is not None and info.target != target:
                if create:
                    if info.active_users:
                        raise RuntimeError(
                            "The previous browser target is still in use; retry after the current call ends"
                        )
                    self._shutdown_session(info)
                    self._session = None
                info = None
            if info is None and create:
                info = SessionInfo(target=target)
                self._session = info
            if info is not None:
                info.active_users += 1
                info.last_active_at = time.time()
        try:
            yield info
        finally:
            if info is not None:
                with self._lifecycle, self._lock:
                    info.active_users -= 1
                    info.last_active_at = time.time()

    def ensure_supervisor(self) -> CDPSupervisor:
        with self._lifecycle:
            with self._lock:
                if self._closed:
                    raise RuntimeError("Browser manager is closed")
                info = self._session
                if info is None:
                    raise RuntimeError("Browser session is not open")
            supervisor = info.supervisor
            if supervisor is not None and supervisor.active:
                try:
                    supervisor.pages.ensure_active_page()
                except RuntimeError:
                    # Closing the last headed window may drop CDP during page recovery.
                    if supervisor.active and (info.launch_handle is None or info.launch_handle.poll() is None):
                        raise
                else:
                    return supervisor
            if supervisor is not None and info.active_users > 1:
                raise RuntimeError(
                    "Browser connection is recovering while other tool calls are still using this session"
                )
            self._shutdown_session(info)
            try:
                if isinstance(info.target, Path):
                    info.launch_handle = launch_chromium(profile_dir=info.target)
                    cdp_url = info.launch_handle.cdp_url
                else:
                    cdp_url = _resolve_cdp_override(info.target)
                policy, timeout_s = get_dialog_policy_config()
                info.supervisor = CDPSupervisor(
                    cdp_url=cdp_url,
                    launch_handle=info.launch_handle,
                    dialog_policy=policy,
                    dialog_timeout_s=timeout_s,
                )
                info.supervisor.start()
                return info.supervisor
            except BaseException:
                try:
                    self._shutdown_session(info)
                except Exception:
                    logger.exception("Browser startup cleanup failed")
                raise

    def _shutdown_session(self, info: SessionInfo) -> None:
        try:
            if info.supervisor is not None:
                info.supervisor.stop()
        finally:
            info.supervisor = None
            if info.launch_handle is not None:
                info.launch_handle.terminate()
                info.launch_handle = None

    def cleanup_idle(self) -> None:
        cutoff = time.time() - inactivity_timeout_s(self._current_config().config)
        with self._lock:
            info = self._session
            if info is not None and (info.active_users or info.last_active_at >= cutoff):
                info = None
        if info is not None:
            with self._lifecycle:
                with self._lock:
                    if self._session is not info or info.active_users or info.last_active_at >= cutoff:
                        return
                    self._session = None
                logger.info("Closing inactive browser session")
                self._shutdown_session(info)

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
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        with self._lifecycle:
            with self._lock:
                info, self._session = self._session, None
            if info is not None:
                try:
                    self._shutdown_session(info)
                except Exception:
                    logger.exception("Browser session shutdown failed")
        self.camofox.close()
        try:
            cleanup_old_profiles(root=profile_root(self._current_config().config))
        except Exception:
            logger.exception("Browser profile cleanup failed")
