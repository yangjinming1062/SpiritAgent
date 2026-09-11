import contextlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from utils import (
    CREATE_NO_WINDOW,
    cfg_get,
    is_truthy_value,
    kill_tree,
    load_config,
)

from ..profile_manager import is_profile_locked

logger = logging.getLogger(__name__)


class BrowserLaunchError(Exception):
    """浏览器启动失败异常。"""


class NativeBrowserProcess:
    """包装原生启动的 Chromium 进程及其 CDP 端点信息。"""

    def __init__(self, proc: subprocess.Popen, pid: int, cdp_url: str, port: int, profile_dir: Path) -> None:
        self.proc = proc
        self.pid = pid
        self.cdp_url = cdp_url
        self.port = port
        self.profile_dir = profile_dir

    def terminate(self, timeout: float = 5.0) -> None:
        try:
            kill_tree(self.pid, graceful_timeout=1.0, force_timeout=2.0)
        except Exception as e:
            logger.debug("Error killing browser process tree %s: %s", self.pid, e)


def find_browser_binary() -> Path | None:
    """按 Edge → Chrome → Brave → Chromium 顺序探测本地浏览器；配置文件可显式覆盖。"""
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if isinstance(browser_cfg, dict):
            custom_path = browser_cfg.get("executable_path")
            if custom_path:
                p = Path(custom_path)
                if p.is_file():
                    return p
    except Exception as e:
        logger.debug("Could not read executable_path from config: %s", e)

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
    try:
        import winreg

        for subkey, val_name, exe_name in reg_keys:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                    try:
                        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as k:
                            loc, _ = winreg.QueryValueEx(k, val_name)
                            if loc:
                                exe_path = Path(loc) / exe_name
                                if exe_path.is_file():
                                    return exe_path
                    except OSError:
                        pass
    except Exception:
        pass

    return None


def _find_browser_macos() -> Path | None:
    candidates = [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return Path(c)
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
    try:
        browser_cfg = cfg_get(load_config(), "browser", default={})
        if isinstance(browser_cfg, dict) and "headless" in browser_cfg:
            return is_truthy_value(browser_cfg["headless"], default=False)
    except Exception:
        pass
    return False


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

    popen_kwargs: dict = {
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
            except Exception:
                pass

        time.sleep(0.1)

    if port is None or not ws_path:
        try:
            kill_tree(proc.pid, force=True)
        except Exception as e:
            logger.debug("kill_tree on launch timeout failed: %s", e)
        raise BrowserLaunchError(
            f"Timed out waiting for DevToolsActivePort in {profile_dir} after {startup_timeout_s}s",
        )

    cdp_url = f"ws://127.0.0.1:{port}/{ws_path.lstrip('/')}"
    return NativeBrowserProcess(proc=proc, pid=proc.pid, cdp_url=cdp_url, port=port, profile_dir=profile_dir)
