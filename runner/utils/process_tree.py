import contextlib
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import psutil

from .constants import IS_WINDOWS
from .pid import kill_tree, pid_exists

logger = logging.getLogger(__name__)

ProcessTarget = int | subprocess.Popen[Any]

SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
SIGTERM = getattr(signal, "SIGTERM", 15)


@dataclass(frozen=True)
class TerminationResult:
    target_pid: int
    pgid: int | None
    escalated: bool
    elapsed_seconds: float


def terminate_tree(
    target: ProcessTarget,
    *,
    graceful_timeout: float = 1.0,
    force_timeout: float = 2.0,
    escalate: bool = False,
    pgid: int | None = None,
) -> TerminationResult:
    """软杀 → 等待 → 可选强杀 → psutil 兜底."""
    pid = _resolve_pid(target)
    if IS_WINDOWS:
        return _terminate_tree_windows(target, pid, graceful_timeout, force_timeout, escalate)
    return _terminate_tree_posix(target, pid, graceful_timeout, force_timeout, escalate, pgid)


def _resolve_pid(target: ProcessTarget) -> int:
    if isinstance(target, subprocess.Popen):
        return int(target.pid)
    return int(target)


def _terminate_tree_posix(
    target: ProcessTarget,
    pid: int,
    graceful_timeout: float,
    force_timeout: float,
    escalate: bool,
    hinted_pgid: int | None,
) -> TerminationResult:
    start = time.monotonic()

    resolved_pgid = hinted_pgid
    if resolved_pgid is None and isinstance(target, subprocess.Popen):
        resolved_pgid = getattr(target, "_spiritagent_pgid", None)
    if resolved_pgid is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            resolved_pgid = os.getpgid(pid)
    if resolved_pgid is None:
        resolved_pgid = pid

    # 必须在 killpg 之前快照 —— 父进程死后句柄失效, 孙子被 init 接管即无法找回。
    orphan_pids: list[int] = []
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        parent = psutil.Process(pid)
        orphan_pids = [c.pid for c in parent.children(recursive=True)]

    runner_pgid = os.getpgrp() if hasattr(os, "getpgrp") else None
    can_signal_group = (
        resolved_pgid is not None and resolved_pgid > 1 and (runner_pgid is None or resolved_pgid != runner_pgid)
    )

    main_group_exited = True
    if can_signal_group and resolved_pgid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(resolved_pgid, SIGTERM)
        main_group_exited = _wait_for_pgroup(target, resolved_pgid, graceful_timeout)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, SIGTERM)
        main_group_exited = _wait_for_pid(target, pid, graceful_timeout)

    escalated = False
    if not main_group_exited and escalate:
        if can_signal_group and resolved_pgid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(resolved_pgid, SIGKILL)
            _wait_for_pgroup(target, resolved_pgid, force_timeout)
        else:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, SIGKILL)
            _wait_for_pid(target, pid, force_timeout)
        escalated = True

    # 不能因 main_group_exited=True 就 return —— 孙子若 setsid 逃出主 pgid 仍存活。
    for orphan_pid in orphan_pids:
        if not pid_exists(orphan_pid):
            continue
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(orphan_pid, SIGTERM)

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not any(pid_exists(op) for op in orphan_pids):
            break
        time.sleep(0.05)
    for orphan_pid in orphan_pids:
        if not pid_exists(orphan_pid):
            continue
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(orphan_pid, SIGKILL)

    return TerminationResult(pid, resolved_pgid if can_signal_group else None, escalated, time.monotonic() - start)


def _wait_for_pgroup(target: ProcessTarget, pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if isinstance(target, subprocess.Popen):
            target.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            pass
        time.sleep(0.05)
    if isinstance(target, subprocess.Popen):
        target.poll()
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        pass
    return False


def _wait_for_pid(target: ProcessTarget, pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if isinstance(target, subprocess.Popen):
            if target.poll() is not None:
                return True
        elif not pid_exists(pid):
            return True
        time.sleep(0.05)
    if isinstance(target, subprocess.Popen):
        return target.poll() is not None
    return not pid_exists(pid)


def _terminate_tree_windows(
    target: ProcessTarget,
    pid: int,
    graceful_timeout: float,
    force_timeout: float,
    escalate: bool,
) -> TerminationResult:
    start = time.monotonic()
    wait_fn = target.wait if isinstance(target, subprocess.Popen) else None
    escalated = False

    kill_tree(pid, force=False)
    try:
        if wait_fn is not None:
            wait_fn(timeout=graceful_timeout)
        else:
            psutil.Process(pid).wait(timeout=graceful_timeout)
    # NoSuchProcess 与 TimeoutExpired 分开: 前者表示软杀已成功, 不应再触发 force。
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
        kill_tree(pid, force=True)
        escalated = True

    if escalate and not (wait_fn is not None and target.poll() is not None) and pid_exists(pid):
        try:
            if wait_fn is not None:
                wait_fn(timeout=force_timeout)
            else:
                psutil.Process(pid).wait(timeout=force_timeout)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
            kill_tree(pid, force=True, timeout=force_timeout)
            escalated = True

    return TerminationResult(pid, None, escalated, time.monotonic() - start)
