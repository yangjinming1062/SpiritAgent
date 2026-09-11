import contextlib
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS: bool = sys.platform == "win32"
"""平台标记：Windows 为 True，POSIX 为 False。"""

IS_MACOS: bool = sys.platform == "darwin"
"""平台标记：macOS 为 True，其他平台为 False。"""

CREATE_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
"""Windows ``creationflags`` 用子进程标志；POSIX 上为 0（无操作）。"""


def get_spiritagent_home() -> Path:
    if override := os.environ.get("SPIRITAGENT_HOME"):
        return Path(override)
    if sys.platform == "win32" and (local_appdata := os.environ.get("LOCALAPPDATA")):
        return Path(local_appdata) / "SpiritAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpiritAgent"
    return Path.home() / ".spiritagent"


def get_spiritagent_home_override() -> str | None:
    return os.environ.get("SPIRITAGENT_HOME") or None


def get_subprocess_home() -> Path:
    return Path(override) if (override := os.environ.get("SPIRITAGENT_SUBPROCESS_HOME")) else get_spiritagent_home()


def get_spiritagent_dir(new_subpath: str | None = None, old_name: str | None = None) -> Path:
    base = get_spiritagent_home()
    new_path = base / new_subpath if new_subpath else None
    old_path = base / old_name if old_name else None
    if new_path and new_path.is_dir():
        return new_path
    if old_path and old_path.is_dir():
        return old_path
    return new_path or old_path or base


def get_skills_dir() -> Path:
    return get_spiritagent_home() / "skills"


def secure_parent_dir(path: str | Path) -> None:
    """确保 ``path`` 的父目录存在并设置 ``0700`` 权限（仅 POSIX 生效）。"""
    parent = Path(path).parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(parent, 0o700)
