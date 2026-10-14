"""业务操作交付的文件结果；HTTP 响应由入口构造。"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeferredCleanup:
    function: Callable[[], object]


@dataclass(frozen=True)
class FileResult:
    path: str | Path
    media_type: str | None = None
    filename: str | None = None
    headers: dict[str, str] | None = None
    background: DeferredCleanup | None = None
