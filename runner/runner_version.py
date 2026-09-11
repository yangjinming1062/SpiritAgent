import contextlib
import re
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_LOCK = threading.Lock()
_CACHED_VERSION: str | None = None


def _read_source_tree_version() -> str | None:
    """从源码树 ``pyproject.toml`` 读版本; 仅在直接跑 checkout 时存在, wheel 安装没有旁边这份 pyproject, 必须走包元数据。"""
    pyproject = Path(__file__).resolve().parent / "pyproject.toml"
    text = ""
    with contextlib.suppress(OSError):
        text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE) if text else None
    return match.group(1) if match else None


def _read_wheel_version() -> str:
    """解析 Runner 自身版本: 优先安装元数据(wheel 构建时打入), 源码树 pyproject.toml 兜底 — 避免两份 ``__version__`` 字面量走偏; 每进程至多一次。"""
    global _CACHED_VERSION
    with _LOCK:
        if _CACHED_VERSION is not None:
            return _CACHED_VERSION
        resolved = _read_source_tree_version() or _safe_metadata_version()
        _CACHED_VERSION = resolved or "0.0.0+unknown"
    return _CACHED_VERSION


def _safe_metadata_version() -> str | None:
    with contextlib.suppress(PackageNotFoundError):
        return version("spirit-agent")
    return None


__version__ = _read_wheel_version()
