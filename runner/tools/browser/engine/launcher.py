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

import psutil
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

    def __init__(
        self,
        proc: subprocess.Popen[Any],
        cdp_url: str,
        *,
        browser_process: psutil.Process | None = None,
    ) -> None:
        self.proc = proc
        self.cdp_url = cdp_url
        self._browser_process = browser_process
        self._terminated = False
        self._lock = threading.Lock()

    @property
    def pid(self) -> int:
        return self._browser_process.pid if self._browser_process is not None else self.proc.pid

    def poll(self) -> int | None:
        """Windows 浏览器正常重启后以接管的主进程为准，不把启动器退出当作浏览器死亡。"""
        if self._browser_process is None:
            return self.proc.poll()
        try:
            if not self._browser_process.is_running():
                return 0
            code = self._browser_process.wait(timeout=0)
        except psutil.TimeoutExpired:
            return None
        except psutil.NoSuchProcess:
            return 0
        return code if code is not None else 0

    def terminate(self) -> None:
        """结束浏览器进程树（幂等）；仅当 PID 已不存在时向原 pgid 补 SIGKILL，避免 PID 复用误杀。"""
        with self._lock:
            if self._terminated:
                return
            self._terminated = True
        posix = sys.platform != "win32"
        if self.poll() is not None:
            if posix and not pid_exists(self.proc.pid):
                with contextlib.suppress(OSError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
            return
        try:
            terminate_tree(
                self._browser_process.pid if self._browser_process is not None else self.proc,
                graceful_timeout=3.0,
                force_timeout=2.0,
                escalate=True,
                pgid=self.proc.pid if posix else None,
            )
        except Exception as e:
            logger.warning("Error killing browser process tree %s: %s", self.pid, e)


def _find_relaunched_browser(executable: Path, profile_dir: Path, launched_at: float) -> psutil.Process | None:
    """仅接管本次启动、同可执行文件且使用同一独立 profile 的 Windows 主进程。"""
    expected_exe = os.path.normcase(str(executable.resolve()))
    expected_profile = os.path.normcase(str(profile_dir.resolve()))
    for pid in psutil.pids():
        try:
            # 不复用 process_iter 的缓存对象，兼容启动器退出期间的 PID/命令行变化。
            process = psutil.Process(pid)
            if process.create_time() < launched_at - 0.1 or os.path.normcase(process.exe()) != expected_exe:
                continue
            args = process.cmdline()
            if any(arg.startswith("--type=") for arg in args):
                continue
            profile_arg = next((arg.partition("=")[2] for arg in args if arg.startswith("--user-data-dir=")), None)
            if (
                profile_arg is not None
                and os.path.normcase(str(Path(profile_arg.strip('"')).resolve())) == expected_profile
            ):
                return process
        except (psutil.Error, OSError):
            continue
    return None


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
    # 用户级安装位于 ~/Applications。
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

    # 删旧 DevToolsActivePort 防读残留端口。
    active_port_file = profile_dir / "DevToolsActivePort"
    if active_port_file.is_file():
        with contextlib.suppress(OSError):
            active_port_file.unlink()

    launched_at = time.time()
    try:
        proc = subprocess.Popen(args, **popen_kwargs)
    except Exception as e:
        raise BrowserLaunchError(f"Failed to spawn browser process {exe}: {e}") from e

    deadline = time.monotonic() + startup_timeout_s
    port: int | None = None
    ws_path: str = ""
    browser_process: psutil.Process | None = None

    try:
        while time.monotonic() < deadline:
            if sys.platform == "win32" and proc.poll() == 0 and browser_process is None:
                browser_process = _find_relaunched_browser(exe, profile_dir, launched_at)
            if active_port_file.is_file():
                try:
                    content = active_port_file.read_text(encoding="utf-8").strip()
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    if len(lines) >= 2 and lines[0].isdigit() and 1 <= int(lines[0]) <= 65535:
                        port = int(lines[0])
                        ws_path = lines[1]
                        if proc.poll() is None or browser_process is not None:
                            break
                except (OSError, ValueError):
                    pass  # 浏览器可能正在写入，下一轮重读
            if browser_process is not None:
                if not browser_process.is_running():
                    raise BrowserLaunchError("Relaunched browser process exited before its CDP endpoint was ready")
            elif (code := proc.poll()) is not None and (sys.platform != "win32" or code != 0):
                raise BrowserLaunchError(f"Browser process exited prematurely with code {code}")
            time.sleep(0.1)

        if port is None or not ws_path or (proc.poll() is not None and browser_process is None):
            raise BrowserLaunchError(
                f"Timed out waiting for a live browser and DevToolsActivePort in {profile_dir} after {startup_timeout_s}s",
            )
    except BaseException:
        if browser_process is None and sys.platform == "win32" and proc.poll() == 0:
            browser_process = _find_relaunched_browser(exe, profile_dir, launched_at)
        NativeBrowserProcess(proc, "", browser_process=browser_process).terminate()
        raise

    cdp_url = f"ws://127.0.0.1:{port}/{ws_path.lstrip('/')}"
    return NativeBrowserProcess(proc=proc, cdp_url=cdp_url, browser_process=browser_process)
