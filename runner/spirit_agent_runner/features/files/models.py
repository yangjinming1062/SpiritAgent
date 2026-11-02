from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MAX_MATCH_CONTENT = 500
MAX_EDIT_BYTES = 10 * 1024 * 1024
BINARY_FILE_ERROR = "Binary file — read_file and patch only handle text."


@dataclass
class ReadResult:
    content: str = ""
    total_lines: int = 0
    file_size: int = 0
    truncated: bool = False
    hint: str | None = None
    is_binary: bool = False
    error: str | None = None
    similar_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != []}


@dataclass
class WriteResult:
    bytes_written: int = 0
    dirs_created: bool = False
    lint: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class PatchResult:
    success: bool = False
    diff: str = ""
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    lint: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success} | {
            k: v
            for k in ("diff", "files_modified", "files_created", "files_deleted", "lint", "error")
            if (v := getattr(self, k))
        }


@dataclass
class SearchMatch:
    path: str
    line_number: int
    content: str


@dataclass
class SearchResult:
    matches: list[SearchMatch] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    truncated: bool = False
    hint: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return (
            {"total_count": self.total_count}
            | (
                {"matches": [{"path": m.path, "line": m.line_number, "content": m.content} for m in self.matches]}
                if self.matches
                else {}
            )
            | {k: v for k in ("files", "counts", "hint", "error") if (v := getattr(self, k))}
            | ({"truncated": True} if self.truncated else {})
        )


@dataclass
class ListResult:
    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class LintResult:
    success: bool = True
    skipped: bool = False
    output: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        if self.skipped:
            return {"status": "skipped", "message": self.message}
        return {"status": "ok" if self.success else "error", "output": self.output} | (
            {"message": self.message} if self.message else {}
        )


@dataclass
class ExecuteResult:
    """执行 shell 命令的结果。"""

    stdout: str = ""
    exit_code: int = 0


DEFAULT_READ_LIMIT = 500


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass(slots=True)
class HunkLine:
    prefix: str  # 取 "+"、"-" 或 " "
    content: str


@dataclass(slots=True)
class Hunk:
    context_hint: str | None = None
    lines: list[HunkLine] = field(default_factory=list)


@dataclass(slots=True)
class PatchOperation:
    operation: OperationType
    file_path: str
    new_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)


@dataclass(slots=True)
class _OpOutcome:
    error: str | None = None
    diff: str = ""
    lint: dict[str, Any] | None = None
