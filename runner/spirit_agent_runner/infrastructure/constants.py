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


def get_spiritagent_dir(subpath: str) -> Path:
    """解析 $SPIRITAGENT_HOME 下的固定子目录路径；不隐式创建目录，由写入方按需 mkdir。"""
    return get_spiritagent_home() / subpath


def get_skills_dir() -> Path:
    return get_spiritagent_home() / "skills"
