import contextlib
import logging
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from utils import cfg_get, get_spiritagent_home, load_config, pid_exists

logger = logging.getLogger(__name__)

# 72h 对齐录屏保留；更老 profile 下次 GC 回收。
DEFAULT_RETENTION_HOURS = 72

_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def _profile_root() -> Path:
    cfg_root = cfg_get(load_config(), "browser", "profile_dir", default="")
    return Path(str(cfg_root)) if cfg_root else get_spiritagent_home() / "browser_profiles"


def resolve_profile_dir(profile_name: str = "default") -> Path:
    """返回 profile_name 对应的磁盘目录路径（按需创建），是否真正使用由调用方决定。"""
    target = _profile_root() / profile_name
    target.mkdir(parents=True, exist_ok=True)
    return target


def _singleton_owner(profile_dir: Path) -> tuple[str, int] | None:
    """POSIX 的 ``SingletonLock`` 是指向 ``<hostname>-<pid>`` 的悬空符号链接，不能用 ``exists()`` 判断。"""
    try:
        target = os.readlink(profile_dir / "SingletonLock")
    except OSError:
        return None
    host, _, pid = target.rpartition("-")
    return (host, int(pid)) if pid.isdigit() else None


def is_profile_locked(profile_dir: Path) -> bool:
    """存活浏览器持有则 True；崩溃残留锁视为未占用以便复用。"""
    if sys.platform == "win32":
        return (profile_dir / "lockfile").exists()
    owner = _singleton_owner(profile_dir)
    return owner is not None and pid_exists(owner[1])


def release_foreign_lock(profile_dir: Path) -> None:
    """清除其他主机名且进程已死的残留锁；否则换网后该 profile 永远起不来。"""
    if sys.platform == "win32":
        return
    owner = _singleton_owner(profile_dir)
    if owner is None or owner[0] == socket.gethostname() or pid_exists(owner[1]):
        return
    logger.info("Removing stale browser profile lock from host %s in %s", owner[0], profile_dir)
    for name in _SINGLETON_FILES:
        with contextlib.suppress(FileNotFoundError):
            (profile_dir / name).unlink()


def cleanup_old_profiles(retention_hours: int = DEFAULT_RETENTION_HOURS) -> int:
    """删除 mtime 早于 retention_hours 且未被占用的 profile 目录；只删 profile 根下看起来像 Chromium profile 的子目录，避免误删无关兄弟目录。"""
    cutoff = time.time() - retention_hours * 3600
    deleted = 0
    root = _profile_root()
    if not root.is_dir():
        return 0

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if not _looks_like_chromium_profile(entry) or is_profile_locked(entry):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            deleted += 1
        except OSError as e:
            logger.debug("Failed to clean profile %s: %s", entry, e)
    return deleted


def _looks_like_chromium_profile(path: Path) -> bool:
    """``path`` 看起来像 Chromium user-data-dir 时返回 True。"""
    return (path / "Local State").is_file() or (path / "Default" / "Preferences").is_file()
