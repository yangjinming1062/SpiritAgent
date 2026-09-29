import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _resolve_version() -> str:
    """Runner 自身版本：源码树 ``pyproject.toml`` 优先（直接运行 checkout 时），否则取 wheel 安装元数据。"""
    try:
        text = (Path(__file__).resolve().parent / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        text = ""
    if match := re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE):
        return match.group(1)
    try:
        return version("spirit-agent")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()
