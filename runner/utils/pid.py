import enum
import subprocess

import psutil

from .constants import CREATE_NO_WINDOW, IS_WINDOWS


class PidState(enum.Enum):
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    TRANSIENT_UNKNOWN = "transient"


def pid_state(pid: int | None) -> PidState:
    """探测 PID 状态，区分存在、不存在与瞬时异常。"""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return PidState.NOT_FOUND
    try:
        return PidState.EXISTS if psutil.pid_exists(pid) else PidState.NOT_FOUND
    except psutil.AccessDenied:
        return PidState.EXISTS
    except psutil.NoSuchProcess:
        return PidState.NOT_FOUND
    except (psutil.TimeoutExpired, psutil.Error, OSError):
        return PidState.TRANSIENT_UNKNOWN


def pid_exists(pid: int | None) -> bool:
    s = pid_state(pid)
    return s is PidState.EXISTS or s is PidState.TRANSIENT_UNKNOWN


def kill_tree(pid: int | None, *, force: bool = True, timeout: float = 10.0) -> bool:
    """在 Windows 上通过 ``taskkill /T [/F]`` 终结进程树；进程已消失时返回 True。POSIX 由调用方走 psutil/os.killpg。"""
    if pid is None:
        return False
    if not IS_WINDOWS:
        raise NotImplementedError("kill_tree is Windows-only; use psutil/os.killpg for POSIX tree-kill")
    args = ["/PID", str(pid), "/T"]
    if force:
        args.append("/F")

    def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["taskkill", *cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    result = _run(args)
    if result is not None and result.returncode in (0, 128):
        return True
    if result is None and not force:
        # 软杀超时——直接升级到强杀，避免调用方再走一遍。
        result = _run([*args, "/F"])
        if result is not None and result.returncode in (0, 128):
            return True
    return False
