import logging
import shutil
import time
from pathlib import Path

from utils import cfg_get, get_spiritagent_home, load_config

logger = logging.getLogger(__name__)

# Chromium 用来声明「单一 owner」的 profile 锁文件——启动时任一存在表示另有实例在用。
_PROFILE_LOCK_FILES = ("SingletonLock", "SingletonCookie", "LOCK")

# 72h 对齐自动录屏保留策略；更老的 profile 在下一次 GC tick 回收。
DEFAULT_RETENTION_HOURS = 72

# SingletonLock 等被视作「新鲜」的最大时长——90s 远超真实 Chrome 启动时间、又远低于 profile GC 保留期，避免把新锁误判为陈旧锁。
_LOCK_FRESHNESS_S = 90.0


def _has_lock_file(path: Path) -> bool:
    """``path`` 下存在任意 Chromium profile 锁文件时返回 True。"""
    return any((path / lock).exists() for lock in _PROFILE_LOCK_FILES)


def resolve_profile_dir(profile_name: str = "default") -> Path:
    """返回 profile_name 对应的磁盘目录路径（按需创建），是否真正使用由调用方决定。"""
    try:
        cfg_root = cfg_get(load_config(), "browser", "profile_dir", default="")
    except Exception as e:
        logger.debug("Could not read browser.profile_dir from config: %s", e)
        cfg_root = ""

    base = Path(cfg_root) if cfg_root else get_spiritagent_home() / "browser_profiles"
    target = base / profile_name
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_profile_locked(profile_dir: Path) -> bool:
    """锁文件在 ``_LOCK_FRESHNESS_S`` 秒内被修改过返回 True（说明有别的浏览器实例在用）；过期锁视为残留以便复用持久 profile。"""
    if not profile_dir.is_dir():
        return False
    cutoff = time.time() - _LOCK_FRESHNESS_S
    for name in _PROFILE_LOCK_FILES:
        path = profile_dir / name
        if path.exists() and path.stat().st_mtime >= cutoff:
            return True
    return False


def cleanup_old_profiles(retention_hours: int = DEFAULT_RETENTION_HOURS) -> int:
    """删除 mtime 早于 retention_hours 的 profile 目录；只在 profile 根下扫描、且仅删看起来像 Chromium profile 的子目录，避免误删无关兄弟目录。"""
    cutoff = time.time() - retention_hours * 3600
    deleted = 0
    try:
        root = resolve_profile_dir().parent
    except Exception as e:
        logger.debug("Could not resolve profile root for cleanup: %s", e)
        return 0
    if not root.is_dir():
        return 0

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if not _looks_like_chromium_profile(entry):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            deleted += 1
        except Exception as e:
            logger.debug("Failed to clean profile %s: %s", entry, e)
    return deleted


def _looks_like_chromium_profile(path: Path) -> bool:
    """``path`` 看起来像 Chromium user-data-dir 时返回 True（含 Default/Preferences 或锁文件）。"""
    return (path / "Default" / "Preferences").is_file() or _has_lock_file(path)
