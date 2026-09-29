import contextlib
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from utils import (
    CREATE_NO_WINDOW,
    cfg_get,
    is_truthy_value,
    load_config,
    pid_exists,
    terminate_tree,
)

from ..profile_manager import is_profile_locked, release_foreign_lock

logger = logging.getLogger(__name__)


class BrowserLaunchError(Exception):
    """浏览器启动失败异常。"""


class NativeBrowserProcess:
    """原生启动的 Chromium 进程及其 CDP 端点。"""

    def __init__(self, proc: subprocess.Popen[Any], cdp_url: str) -> None:
        self.proc = proc
        self.cdp_url = cdp_url
        self._terminated = False
        self._lock = threading.Lock()

    def terminate(self) -> None:
        """结束浏览器进程树；幂等。

        主进程已退出并被回收后 PID 可能被复用，不能再按 PID 解析进程树。POSIX 下原进程组的 pgid 等于该 PID，
        组内仍有残留成员时该 PID 不会被分配给新进程；因此只有当前不存在该 PID 的进程时，才向原进程组补发 SIGKILL。
        """
        with self._lock:
            if self._terminated:
                return
            self._terminated = True
        posix = sys.platform != "win32"
        if self.proc.poll() is not None:
            if posix and not pid_exists(self.proc.pid):
                with contextlib.suppress(OSError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
            return
        try:
            terminate_tree(
                self.proc,
                graceful_timeout=3.0,
                force_timeout=2.0,
                escalate=True,
                pgid=self.proc.pid if posix else None,
            )
        except Exception as e:
            logger.warning("Error killing browser process tree %s: %s", self.proc.pid, e)


def find_browser_binary() -> Path | None:
    """按 Edge → Chrome → Brave → Chromium 顺序探测本地浏览器；配置文件可显式覆盖。"""
    if custom_path := cfg_get(load_config(), "browser", "executable_path"):
        p = Path(str(custom_path))
        if p.is_file():
            return p
        logger.debug("browser.executable_path %s is not a file; falling back to auto-detection", p)

    if sys.platform == "win32":
        return _find_browser_windows()
    if sys.platform == "darwin":
        return _find_browser_macos()
    return _find_browser_linux()


def _find_browser_windows() -> Path | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chromium.exe"),
        os.path.expandvars(r"%ProgramFiles%\Chromium\Application\chromium.exe"),
    ]

    for c in candidates:
        if c and os.path.isfile(c):
            return Path(c)

    reg_keys = [
        (r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "location", r"msedge.exe"),
        (r"SOFTWARE\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}", "location", r"chrome.exe"),
        (r"SOFTWARE\BraveSoftware\Update\Clients\{AFE6A462-C574-4B8A-AF43-4CC60DF4563B}", "location", r"brave.exe"),
    ]
    import winreg

    for subkey, val_name, exe_name in reg_keys:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as k:
                        loc, _ = winreg.QueryValueEx(k, val_name)
                except OSError:
                    continue
                if isinstance(loc, str) and loc and (exe_path := Path(loc) / exe_name).is_file():
                    return exe_path

    return None


def _find_browser_macos() -> Path | None:
    # 未用管理员权限安装时应用位于用户级 ~/Applications。
    bundles = (
        "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "Google Chrome.app/Contents/MacOS/Google Chrome",
        "Brave Browser.app/Contents/MacOS/Brave Browser",
        "Chromium.app/Contents/MacOS/Chromium",
    )
    for bundle in bundles:
        for apps_dir in (Path("/Applications"), Path.home() / "Applications"):
            if (candidate := apps_dir / bundle).is_file():
                return candidate
    return None


def _find_browser_linux() -> Path | None:
    for name in (
        "microsoft-edge",
        "microsoft-edge-stable",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
    ):
        path = shutil.which(name)
        if path and os.path.isfile(path):
            return Path(path)

    for fb in ("/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/google-chrome"):
        if os.path.isfile(fb):
            return Path(fb)
    return None


def _is_headless_configured() -> bool:
    """读取配置是否开启 headless（默认 False，即 headed）。"""
    return is_truthy_value(cfg_get(load_config(), "browser", "headless"), default=False)


def launch_chromium(
    *,
    executable: Path | None = None,
    profile_dir: Path,
    headless: bool | None = None,
    extra_args: list[str] | None = None,
    startup_timeout_s: float = 20.0,
) -> NativeBrowserProcess:
    """启动原生 Chromium 进程并等待 DevToolsActivePort 就绪。"""
    exe = executable or find_browser_binary()
    if exe is None or not exe.is_file():
        raise BrowserLaunchError(
            "No supported browser (Edge, Chrome, Brave, Chromium) found. Install one or set 'browser.executable_path'.",
        )

    profile_dir.mkdir(parents=True, exist_ok=True)
    release_foreign_lock(profile_dir)
    if is_profile_locked(profile_dir):
        profile_dir = profile_dir.parent / f"{profile_dir.name}_{secrets.token_hex(4)}"
        profile_dir.mkdir(parents=True, exist_ok=True)

    if headless is None:
        headless = _is_headless_configured()

    args = [
        str(exe),
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter",
        "--no-pings",
        "--password-store=basic",
    ]

    if headless:
        args.append("--headless=new")

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.extend(["--no-sandbox", "--disable-dev-shm-usage"])

    if extra_args:
        args.extend(extra_args)

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }

    if sys.platform == "win32":
        popen_kwargs["close_fds"] = True
        if headless:
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESTDHANDLES
            popen_kwargs["startupinfo"] = si
    else:
        popen_kwargs["start_new_session"] = True

    # 删除旧的 DevToolsActivePort 文件，避免读到上次进程残留的端口
    active_port_file = profile_dir / "DevToolsActivePort"
    if active_port_file.is_file():
        with contextlib.suppress(OSError):
            active_port_file.unlink()

    try:
        proc = subprocess.Popen(args, **popen_kwargs)
    except Exception as e:
        raise BrowserLaunchError(f"Failed to spawn browser process {exe}: {e}") from e

    deadline = time.monotonic() + startup_timeout_s
    port: int | None = None
    ws_path: str = ""

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise BrowserLaunchError(f"Browser process exited prematurely with code {proc.returncode}")

        if active_port_file.is_file():
            try:
                content = active_port_file.read_text(encoding="utf-8").strip()
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                if len(lines) >= 2 and lines[0].isdigit():
                    port = int(lines[0])
                    ws_path = lines[1]
                    break
            except (OSError, ValueError):
                pass  # 浏览器可能正在写入，下一轮重读

        time.sleep(0.1)

    if port is None or not ws_path:
        try:
            terminate_tree(proc, graceful_timeout=0.5, force_timeout=1.0, escalate=True)
        except Exception as e:
            logger.debug("terminate_tree on launch timeout failed: %s", e)
        raise BrowserLaunchError(
            f"Timed out waiting for DevToolsActivePort in {profile_dir} after {startup_timeout_s}s",
        )

    cdp_url = f"ws://127.0.0.1:{port}/{ws_path.lstrip('/')}"
    return NativeBrowserProcess(proc=proc, cdp_url=cdp_url)
