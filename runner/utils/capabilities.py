import logging
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any

from .constants import IS_MACOS, IS_WINDOWS

logger = logging.getLogger(__name__)


def _binary_exists(name: str) -> bool:
    return shutil.which(name) is not None


def probe_microphone() -> tuple[bool, str | None]:
    """探测麦克风可用性，返回 (是否可用, 失败原因)。"""
    if IS_WINDOWS or IS_MACOS:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            try:
                devices = sd.query_devices()
            except Exception as e:
                logger.debug("sounddevice query failed: %s", e)
                return False, f"sounddevice query failed: {e}"
            for entry in devices:
                if int(entry.get("max_input_channels", 0)) >= 1:
                    return True, None
            return False, "No audio capture device found"
        except (ImportError, OSError) as e:
            logger.debug("microphone probe sounddevice path failed: %s", e)
            return False, f"sounddevice import/OS error: {e}"
    return False, f"Unsupported platform: {sys.platform}"


def microphone_available() -> bool:
    """轻量麦克风探测：枚举设备，永不打开音频流。"""
    if IS_WINDOWS or IS_MACOS:
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            devices = sd.query_devices()
            return any(int(entry.get("max_input_channels", 0)) >= 1 for entry in devices)
        except (ImportError, OSError):
            return False
    return False


def probe_screen_capture() -> tuple[bool, str | None]:
    """探测截屏能力，返回 (是否可用, 失败原因)。"""
    if IS_WINDOWS:
        try:
            import mss

            with mss.MSS() as sct:
                if len(sct.monitors) > 1:
                    return True, None
                return False, "No active display monitors detected"
        except Exception as e:
            logger.debug("screen capture probe failed: %s", e)
            return False, f"mss capture initialization failed: {e}"
    if IS_MACOS:
        if _binary_exists("screencapture"):
            return True, None
        return False, "screencapture binary not found in PATH"
    return False, f"Unsupported platform: {sys.platform}"


def screen_capture_available() -> bool:
    """当前平台是否具备可用的截屏路径。"""
    if IS_WINDOWS:
        try:
            import mss

            with mss.MSS() as sct:
                return len(sct.monitors) > 1
        except Exception:
            return False
    if IS_MACOS:
        return _binary_exists("screencapture")
    return False


def probe_system_activity() -> tuple[bool, str | None]:
    """探测系统活动检测能力，返回 (是否可用, 失败原因)。"""
    if IS_WINDOWS:
        try:
            import ctypes

            user32 = ctypes.windll.user32

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            if bool(user32.GetLastInputInfo(ctypes.byref(info))):
                return True, None
            return False, "GetLastInputInfo returned false"
        except Exception as e:
            logger.debug("win system-activity probe failed: %s", e)
            return False, f"Win32 GetLastInputInfo failed: {e}"
    if IS_MACOS:
        try:
            import Quartz  # type: ignore[import-not-found]

            d = Quartz.CGSessionCopyCurrentDictionary()
            if d is not None:
                return True, None
            return False, "CGSessionCopyCurrentDictionary returned None"
        except Exception as e:
            logger.debug("macos system-activity probe failed: %s", e)
            return False, f"macOS Quartz probe failed: {e}"
    return False, f"Unsupported platform: {sys.platform}"


def system_activity_available() -> bool:
    """空闲/锁屏/焦点探测在本构建是否可用。"""
    if IS_WINDOWS:
        try:
            import ctypes

            user32 = ctypes.windll.user32

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(info)
            return bool(user32.GetLastInputInfo(ctypes.byref(info)))
        except Exception:
            return False
    if IS_MACOS:
        try:
            import Quartz  # type: ignore[import-not-found]

            return Quartz.CGSessionCopyCurrentDictionary() is not None
        except Exception:
            return False
    return False


def network_reachable(host: str = "1.1.1.1", port: int = 443, timeout: float = 1.5) -> bool:
    """轻量连通性探测（供 ``spiritagent.info`` 上报网络状态），避免握手超时。"""
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


_SNAPSHOT_TTL_S = 30.0
_snapshot_cache: tuple[float, dict[str, Any], dict[str, Any]] | None = None


def snapshot_with_health() -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (能力布隆, 健康详情) 双份字典，按 TTL 缓存。"""
    global _snapshot_cache
    now = time.monotonic()
    if _snapshot_cache is not None and now - _snapshot_cache[0] < _SNAPSHOT_TTL_S:
        return _snapshot_cache[1], _snapshot_cache[2]

    mic_ok = microphone_available()
    screen_ok = screen_capture_available()
    sys_ok = system_activity_available()

    _, mic_reason = (True, None) if mic_ok else probe_microphone()
    _, screen_reason = (True, None) if screen_ok else probe_screen_capture()
    _, sys_reason = (True, None) if sys_ok else probe_system_activity()

    caps = {
        "microphone": mic_ok,
        "screen_capture": screen_ok,
        "system_activity": sys_ok,
        "platform": sys.platform,
        "python": sys.version.split()[0],
    }

    health = {
        "microphone": {"available": mic_ok, "reason": mic_reason},
        "screen_capture": {"available": screen_ok, "reason": screen_reason},
        "system_activity": {"available": sys_ok, "reason": sys_reason},
    }

    _snapshot_cache = (now, caps, health)
    return caps, health


def snapshot() -> dict[str, Any]:
    """返回握手 / info RPC 上报的完整能力映射。"""
    caps, _ = snapshot_with_health()
    return caps


def snapshot_health() -> dict[str, Any]:
    """返回结构化的能力健康详情。"""
    _, health = snapshot_with_health()
    return health
