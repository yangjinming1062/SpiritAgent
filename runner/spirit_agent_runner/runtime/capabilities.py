import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

from spirit_agent_runner.infrastructure.constants import IS_WINDOWS


def _probe_microphone() -> tuple[bool, str | None]:
    """枚举输入设备判断麦克风可用性，不打开音频流。"""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]

        devices = sd.query_devices()
    except Exception as e:
        return False, f"sounddevice query failed: {e}"
    if any(int(entry.get("max_input_channels", 0)) >= 1 for entry in devices):
        return True, None
    return False, "No audio capture device found"


def _probe_screen_capture() -> tuple[bool, str | None]:
    if IS_WINDOWS:
        try:
            import mss

            with mss.MSS() as sct:
                if len(sct.monitors) > 1:
                    return True, None
                return False, "No active display monitors detected"
        except Exception as e:
            return False, f"mss capture initialization failed: {e}"
    if shutil.which("screencapture") is not None:
        return True, None
    return False, "screencapture binary not found in PATH"


def _probe_system_activity() -> tuple[bool, str | None]:
    """探测空闲 / 锁屏 / 焦点检测所依赖的系统接口。"""
    if IS_WINDOWS:
        try:
            import ctypes

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return True, None
            return False, "GetLastInputInfo returned false"
        except Exception as e:
            return False, f"Win32 GetLastInputInfo failed: {e}"
    try:
        import Quartz  # type: ignore[import-not-found]

        if Quartz.CGSessionCopyCurrentDictionary() is not None:
            return True, None
        return False, "CGSessionCopyCurrentDictionary returned None"
    except Exception as e:
        return False, f"macOS Quartz probe failed: {e}"


def network_reachable(host: str = "1.1.1.1", port: int = 443, timeout: float = 1.5) -> bool:
    """轻量连通性探测（供 ``runner.info`` 上报网络状态），避免握手超时。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def disk_free_bytes(path: str | Path = ".") -> int | None:
    """``path`` 所在文件系统的剩余字节数；无法查询时返回 ``None``。"""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class CapabilityProbe:
    def __init__(self) -> None:
        self._cached: tuple[float, dict[str, Any], dict[str, Any]] | None = None

    def snapshot(self) -> tuple[dict[str, Any], dict[str, Any], bool]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < 30.0:
            return self._cached[1], self._cached[2], False
        try:
            probes = {
                "microphone": _probe_microphone(),
                "screen_capture": _probe_screen_capture(),
                "system_activity": _probe_system_activity(),
            }
            caps: dict[str, Any] = {name: ok for name, (ok, _) in probes.items()}
            caps.update({"platform": sys.platform, "python": sys.version.split()[0]})
            health = {name: {"available": ok, "reason": reason} for name, (ok, reason) in probes.items()}
            self._cached = now, caps, health
            return caps, health, False
        except Exception:
            return {}, {}, True
