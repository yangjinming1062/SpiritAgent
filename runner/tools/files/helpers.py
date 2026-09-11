import ast
import difflib
import json
import os
import re
import threading
import time
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from utils import cfg_get, is_write_denied, load_config, strip_ansi

from ..tool_output_limits import get_max_line_length, get_max_lines
from .binary_extensions import BINARY_EXTENSIONS
from .fuzzy_match import format_no_match_hint, fuzzy_find_and_replace

# ── File State ─────────────────────────────────────────────────────────────

_OSC_SEQUENCE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_FENCE_MARKER_RE = re.compile(r"'?\x07?SPIRITAGENT_FENCE_[A-Za-z0-9]+__\x07?'?")
_SEARCH_LINE_RE = re.compile(r"^([A-Za-z]:)?(.*?):(\d+):(.*)$")
_CONTEXT_DELIM_RE = re.compile(r"-(\d+)-")
_HUNK_HINT_RE = re.compile(r"@@\s*(.+?)\s*@@")


def _strip_terminal_fence_leaks(text: str) -> str:
    """剥离读取文件中残留的终端围栏包裹。"""
    if not text:
        return text
    cleaned_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        had_terminal_wrapper = "SPIRITAGENT_FENCE_" in line or "\x1b]" in line
        cleaned = _OSC_SEQUENCE_RE.sub("", line)
        cleaned = _FENCE_MARKER_RE.sub("", cleaned)
        cleaned = cleaned.replace("\x07", "")
        if had_terminal_wrapper and not cleaned.strip("'\r\n\t "):
            continue
        cleaned_lines.append(cleaned)
    return "".join(cleaned_lines)


def _detect_line_ending(sample: str) -> str | None:
    """返回 ``sample`` 中的主要换行符；无法判断时返回 None。"""
    if not sample:
        return None
    head = sample[:4096]
    if "\r\n" in head:
        return "\r\n"
    if "\n" in head:
        return "\n"
    return None


def _normalize_line_endings(text: str, target: str) -> str:
    """将 ``text`` 中所有换行符统一为 ``target``（``\\n`` 或 ``\\r\\n``）。"""
    lf_normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if target == "\n":
        return lf_normalized
    if target == "\r\n":
        return lf_normalized.replace("\n", "\r\n")
    return text


_UTF8_BOM = "\ufeff"


def _strip_bom(text: str) -> tuple[str, bool]:
    """返回 (去 BOM 文本, 是否有 BOM)。"""
    if text and text.startswith(_UTF8_BOM):
        return text[len(_UTF8_BOM) :], True
    return text, False


def _has_bom(text: str | None) -> bool:
    """若 ``text`` 以 UTF-8 BOM 开头则返回 True。"""
    return bool(text) and text.startswith(_UTF8_BOM)


@dataclass
class ReadResult:
    content: str = ""
    total_lines: int = 0
    file_size: int = 0
    truncated: bool = False
    hint: str | None = None
    is_binary: bool = False
    is_image: bool = False
    base64_content: str | None = None
    mime_type: str | None = None
    dimensions: str | None = None
    error: str | None = None
    similar_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != []}


@dataclass
class WriteResult:
    bytes_written: int = 0
    dirs_created: bool = False
    lint: dict[str, Any] | None = None
    error: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
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

    def to_dict(self) -> dict:
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
    mtime: float = 0.0


@dataclass
class SearchResult:
    matches: list[SearchMatch] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return (
            {"total_count": self.total_count}
            | (
                {"matches": [{"path": m.path, "line": m.line_number, "content": m.content} for m in self.matches]}
                if self.matches
                else {}
            )
            | {k: v for k in ("files", "counts", "error") if (v := getattr(self, k))}
            | ({"truncated": True} if self.truncated else {})
        )


@dataclass
class LintResult:
    success: bool = True
    skipped: bool = False
    output: str = ""
    message: str = ""

    def to_dict(self) -> dict:
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


def _split_tool_diagnostics(output: str) -> tuple[str, str]:
    """把 rg/grep 的诊断行与真正的匹配输出分开。"""
    diagnostics: list[str] = []
    payload: list[str] = []
    for line in output.split("\n"):
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("rg: ") or stripped.startswith("grep: "):
            diagnostics.append(line)
            continue
        if line == "--" or _SEARCH_OUTPUT_RE.match(line):
            payload.append(line)
        else:
            diagnostics.append(line)
    return "\n".join(diagnostics), "\n".join(payload)


_SEARCH_OUTPUT_RE = re.compile(r"^([A-Za-z]:)?[^\s:][^\n]*?[:\-]\d|^[^\s:][^\s]*$")


def _parse_search_context_line(line: str) -> tuple[str, int, str] | None:
    """解析 ``path-line-content`` 形式的 grep/rg 上下文输出。"""
    if not line or line == "--":
        return None
    match = None
    for candidate in _CONTEXT_DELIM_RE.finditer(line):
        match = candidate
    if match is None:
        return None
    path = line[: match.start()]
    if not path:
        return None
    return path, int(match.group(1)), line[match.end() :]


class FileOperations(ABC):
    """跨终端后端的文件操作抽象接口。"""

    @abstractmethod
    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult: ...

    @abstractmethod
    def read_file_raw(self, path: str) -> ReadResult: ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> WriteResult: ...

    @abstractmethod
    def patch_replace(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> PatchResult: ...

    @abstractmethod
    def patch_v4a(self, patch_content: str) -> PatchResult: ...

    @abstractmethod
    def delete_file(self, path: str) -> WriteResult: ...

    @abstractmethod
    def delete_path(self, path: str, recursive: bool = False) -> WriteResult: ...

    @abstractmethod
    def move_file(self, src: str, dst: str) -> WriteResult: ...

    @abstractmethod
    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = 50,
        offset: int = 0,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult: ...


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}

LINTERS = {
    ".py": "python -m py_compile {file} 2>&1",
    ".js": "node --check {file} 2>&1",
    ".ts": "npx tsc --noEmit {file} 2>&1",
    ".go": "go vet {file} 2>&1",
    ".rs": "rustfmt --check {file} 2>&1",
}

_LINTER_UNUSABLE_PATTERNS = {
    "npx": (
        "this is not the tsc command you are looking for",
        "could not determine executable to run",
        "not found in npm registry",
    ),
    "rustfmt": ("no input filename given", "error: not a workspace"),
    "go": ("cannot find package", "go: cannot find main module"),
}


def _looks_like_linter_unusable(base_cmd: str, output: str) -> bool:
    """若 ``output`` 表明 ``base_cmd`` 自身无法运行则返回 True。"""
    patterns = _LINTER_UNUSABLE_PATTERNS.get(base_cmd)
    if not patterns:
        return False
    lower = output.lower()
    return any(p in lower for p in patterns)


def _lint_json_inproc(content: str) -> tuple[bool, str]:
    """进程内 JSON 语法校验。"""
    try:
        json.loads(content)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSONDecodeError: {e.msg} (line {e.lineno}, column {e.colno})"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_yaml_inproc(content: str) -> tuple[bool, str]:
    """进程内 YAML 语法校验。"""
    try:
        yaml.safe_load(content)
        return True, ""
    except yaml.YAMLError as e:
        return False, f"YAMLError: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_toml_inproc(content: str) -> tuple[bool, str]:
    """进程内 TOML 语法校验。"""
    try:
        tomllib.loads(content)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _lint_python_inproc(content: str) -> tuple[bool, str]:
    """通过 ast.parse 进行进程内 Python 语法校验。"""
    try:
        ast.parse(content)
        return True, ""
    except SyntaxError as e:
        loc = f" (line {e.lineno}, column {e.offset})" if e.lineno else ""
        return False, f"{type(e).__name__}: {e.msg}{loc}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


LINTERS_INPROC = {
    ".py": _lint_python_inproc,
    ".json": _lint_json_inproc,
    ".yaml": _lint_yaml_inproc,
    ".yml": _lint_yaml_inproc,
    ".toml": _lint_toml_inproc,
}

MAX_LINES = 2000
MAX_FILE_SIZE = 50 * 1024
DEFAULT_READ_OFFSET = 1
DEFAULT_READ_LIMIT = 500
DEFAULT_SEARCH_OFFSET = 0
DEFAULT_SEARCH_LIMIT = 50


def _coerce_int(value: Any, default: int) -> int:
    """尽力将分页参数转为整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_read_pagination(offset: Any = DEFAULT_READ_OFFSET, limit: Any = DEFAULT_READ_LIMIT) -> tuple[int, int]:
    """返回 read_file 的安全分页边界。"""
    max_lines = get_max_lines()
    normalized_offset = max(1, _coerce_int(offset, DEFAULT_READ_OFFSET))
    normalized_limit = _coerce_int(limit, DEFAULT_READ_LIMIT)
    normalized_limit = max(1, min(normalized_limit, max_lines))
    return normalized_offset, normalized_limit


def normalize_search_pagination(
    offset: Any = DEFAULT_SEARCH_OFFSET,
    limit: Any = DEFAULT_SEARCH_LIMIT,
) -> tuple[int, int]:
    """返回 shell head/tail 管线安全的搜索分页边界。"""
    normalized_offset = max(0, _coerce_int(offset, DEFAULT_SEARCH_OFFSET))
    normalized_limit = max(1, _coerce_int(limit, DEFAULT_SEARCH_LIMIT))
    return normalized_offset, normalized_limit


class ShellFileOperations(FileOperations):
    """通过 shell 命令实现文件操作。"""

    def __init__(self, terminal_env: Any, cwd: str | None = None) -> None:
        self.env = terminal_env
        self.cwd = (
            cwd
            or getattr(terminal_env, "cwd", None)
            or getattr(getattr(terminal_env, "config", None), "cwd", None)
            or "/"
        )
        self._command_cache: dict[str, bool] = {}

    def _exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> ExecuteResult:
        """通过终端后端执行命令。"""
        kwargs = {}
        if timeout:
            kwargs["timeout"] = timeout
        if stdin_data is not None:
            kwargs["stdin_data"] = stdin_data
        effective_cwd = cwd or getattr(self.env, "cwd", None) or self.cwd
        result = self.env.execute(command, cwd=effective_cwd, **kwargs)
        return ExecuteResult(stdout=result.get("output", ""), exit_code=result.get("returncode", 0))

    def _has_command(self, cmd: str) -> bool:
        """检查命令是否在环境中可用（带缓存）。"""
        if cmd not in self._command_cache:
            result = self._exec(f"command -v {cmd} >/dev/null 2>&1 && echo 'yes'")
            self._command_cache[cmd] = result.stdout.strip() == "yes"
        return self._command_cache[cmd]

    def _is_likely_binary(self, path: str, content_sample: str | None = None) -> bool:
        """判断文件是否可能为二进制。"""
        ext = os.path.splitext(path)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return True
        if content_sample:
            non_printable = sum(1 for c in content_sample[:1000] if ord(c) < 32 and c not in "\n\r\t")
            return non_printable / min(len(content_sample), 1000) > 0.30
        return False

    def _is_image(self, path: str) -> bool:
        """判断文件是否为可返回 base64 的图片。"""
        ext = os.path.splitext(path)[1].lower()
        return ext in IMAGE_EXTENSIONS

    def _add_line_numbers(self, content: str, start_line: int = 1) -> str:
        """以 ``LINE_NUM|CONTENT`` 格式给内容加上行号。"""
        max_line_length = get_max_line_length()
        lines = content.split("\n")
        numbered = []
        for i, line in enumerate(lines, start=start_line):
            if len(line) > max_line_length:
                line = line[:max_line_length] + "... [truncated]"
            numbered.append(f"{i}|{line}")
        return "\n".join(numbered)

    def _expand_path(self, path: str) -> str:
        """把 ``~``/``~user`` 形式的 shell 路径展开为绝对路径。"""
        if not path:
            return path
        if path.startswith("~"):
            result = self._exec("echo $HOME")
            if result.exit_code == 0 and result.stdout.strip():
                home = result.stdout.strip()
                if path == "~":
                    return home
                elif path.startswith("~/"):
                    return home + path[1:]
                rest = path[1:]
                slash_idx = rest.find("/")
                username = rest[:slash_idx] if slash_idx >= 0 else rest
                if username and re.fullmatch(r"[a-zA-Z0-9._-]+", username):
                    expand_result = self._exec(f"echo ~{username}")
                    if expand_result.exit_code == 0 and expand_result.stdout.strip():
                        user_home = expand_result.stdout.strip()
                        suffix = path[1 + len(username) :]
                        return user_home + suffix
        return path

    def _escape_shell_arg(self, arg: str) -> str:
        """把字符串转义为 shell 命令可安全使用的形式。"""
        return "'" + arg.replace("'", "'\"'\"'") + "'"

    def _atomic_write(self, path: str, content: str) -> "ExecuteResult":
        """通过「临时文件 + rename」原子写入 ``content`` 到 ``path``。"""
        q_path = self._escape_shell_arg(path)
        parent = os.path.dirname(path) or "."
        q_parent = self._escape_shell_arg(parent)
        tmpl = self._escape_shell_arg(".spiritagent-tmp.XXXXXX")
        # 第二/第三兜底必须把 $d 引起来（第一兜底已正确），不然 $d 含空格或 shell
        # 元字符会断词。第三兜底用 $RANDOM 不用 $$，避免并发写同目录的 PID 冲突。
        script = (
            "set -e; "
            f"d={q_parent}; t={q_path}; "
            'tmp="$(mktemp -p "$d" ' + tmpl + " 2>/dev/null "
            '|| mktemp "$d/.spiritagent-tmp.$RANDOM.XXXXXX" 2>/dev/null '
            '|| { tmp="$d/.spiritagent-tmp.$RANDOM"; : > "$tmp" && echo "$tmp"; })"; '
            '[ -n "$tmp" ] || { echo "atomic write: could not create temp file" >&2; exit 1; }; '
            "trap 'rm -f \"$tmp\"' EXIT; "
            'if [ -e "$t" ]; then '
            'm="$(stat -c%a "$t" 2>/dev/null || stat -f%Lp "$t" 2>/dev/null || true)"; '
            '[ -n "$m" ] && chmod "$m" "$tmp" 2>/dev/null || true; '
            "fi; "
            'cat > "$tmp"; '
            'mv -f "$tmp" "$t"; '
            "trap - EXIT"
        )
        return self._exec(script, stdin_data=content)

    def _detect_file_line_ending(self, path: str, pre_content: str | None = None) -> str | None:
        """检测磁盘上文件的主要换行符。"""
        if pre_content:
            return _detect_line_ending(pre_content)
        head_cmd = f"head -c 4096 {self._escape_shell_arg(path)} 2>/dev/null"
        head_result = self._exec(head_cmd)
        if head_result.exit_code != 0 or not head_result.stdout:
            return None
        return _detect_line_ending(head_result.stdout)

    def _file_has_bom(self, path: str, pre_content: str | None = None) -> bool:
        """磁盘上文件是否以 UTF-8 BOM 开头。"""
        if pre_content is not None:
            return _has_bom(pre_content)
        head_cmd = f"head -c 3 {self._escape_shell_arg(path)} 2>/dev/null"
        head_result = self._exec(head_cmd)
        if head_result.exit_code != 0 or not head_result.stdout:
            return False
        return _has_bom(head_result.stdout)

    def _unified_diff(self, old_content: str, new_content: str, filename: str) -> str:
        """生成新旧内容的 unified diff。"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{filename}", tofile=f"b/{filename}")
        return "".join(diff)

    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        """带分页、二进制检测与行号的文件读取。"""
        path = self._expand_path(path)
        offset, limit = normalize_read_pagination(offset, limit)
        stat_cmd = f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null"
        stat_result = self._exec(stat_cmd)
        if stat_result.exit_code != 0:
            return self._suggest_similar_files(path)
        stat_output = _strip_terminal_fence_leaks(stat_result.stdout)
        try:
            file_size = int(stat_output.strip())
        except ValueError:
            file_size = 0
        if file_size > MAX_FILE_SIZE and limit >= 500:
            # 大文件 + 默认分页：拒绝以防「尾巴循环」耗尽上下文。
            # 当 limit <500 时调用方在做有针对性的分页读取（``sed`` 受 limit 限制），放行。
            return ReadResult(
                file_size=file_size,
                error=(
                    f"File size {file_size:,} bytes exceeds safety cap of {MAX_FILE_SIZE:,} bytes. Read with offset/limit or use the terminal tool for a paginated view."
                ),
            )
        if self._is_image(path):
            return ReadResult(
                is_image=True,
                is_binary=True,
                file_size=file_size,
                hint="Image file detected. Automatically redirected to vision_analyze tool. Use vision_analyze with this file path to inspect the image contents.",
            )
        sample_cmd = f"head -c 1000 {self._escape_shell_arg(path)} 2>/dev/null"
        sample_result = self._exec(sample_cmd)
        sample_output = _strip_terminal_fence_leaks(sample_result.stdout)
        if self._is_likely_binary(path, sample_output):
            return ReadResult(
                is_binary=True,
                file_size=file_size,
                error="Binary file - cannot display as text. Use appropriate tools to handle this file type.",
            )
        end_line = offset + limit - 1
        read_cmd = f"sed -n '{offset},{end_line}p' {self._escape_shell_arg(path)}"
        read_result = self._exec(read_cmd)
        if read_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {read_result.stdout}")
        read_output = _strip_terminal_fence_leaks(read_result.stdout)
        if offset == 1:
            read_output, _ = _strip_bom(read_output)
        wc_cmd = f"wc -l < {self._escape_shell_arg(path)}"
        wc_result = self._exec(wc_cmd)
        wc_output = _strip_terminal_fence_leaks(wc_result.stdout)
        try:
            total_lines = int(wc_output.strip())
        except ValueError:
            total_lines = 0
        truncated = total_lines > end_line
        hint = None
        if truncated:
            hint = f"Use offset={end_line + 1} to continue reading (showing {offset}-{end_line} of {total_lines} lines)"
        return ReadResult(
            content=self._add_line_numbers(read_output, offset),
            total_lines=total_lines,
            file_size=file_size,
            truncated=truncated,
            hint=hint,
        )

    def _suggest_similar_files(self, path: str) -> ReadResult:
        """请求的文件未找到时推荐相似的文件。"""
        dir_path = os.path.dirname(path) or "."
        filename = os.path.basename(path)
        basename_no_ext = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1].lower()
        lower_name = filename.lower()
        ls_cmd = f"ls -1 {self._escape_shell_arg(dir_path)} 2>/dev/null | head -50"
        ls_result = self._exec(ls_cmd)
        scored: list = []
        if ls_result.exit_code == 0 and ls_result.stdout.strip():
            for f in ls_result.stdout.strip().split("\n"):
                if not f:
                    continue
                lf = f.lower()
                score = 0
                if lf == lower_name:
                    score = 100
                elif os.path.splitext(f)[0].lower() == basename_no_ext.lower():
                    score = 90
                elif lf.startswith(lower_name) or lower_name.startswith(lf):
                    score = 70
                elif lower_name in lf:
                    score = 60
                elif lf in lower_name and len(lf) > 2:
                    score = 40
                elif ext and os.path.splitext(f)[1].lower() == ext:
                    common = set(lower_name) & set(lf)
                    if len(common) >= max(len(lower_name), len(lf)) * 0.4:
                        score = 30
                if score > 0:
                    scored.append((score, os.path.join(dir_path, f)))
        scored.sort(key=lambda x: -x[0])
        similar = [fp for _, fp in scored[:5]]
        return ReadResult(error=f"File not found: {path}", similar_files=similar)

    def read_file_raw(self, path: str) -> ReadResult:
        """将完整文件内容读取为纯字符串。"""
        path = self._expand_path(path)
        stat_cmd = f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null"
        stat_result = self._exec(stat_cmd)
        if stat_result.exit_code != 0:
            return self._suggest_similar_files(path)
        stat_output = _strip_terminal_fence_leaks(stat_result.stdout)
        try:
            file_size = int(stat_output.strip())
        except ValueError:
            file_size = 0
        if self._is_image(path):
            return ReadResult(is_image=True, is_binary=True, file_size=file_size)
        sample_result = self._exec(f"head -c 1000 {self._escape_shell_arg(path)} 2>/dev/null")
        sample_output = _strip_terminal_fence_leaks(sample_result.stdout)
        if self._is_likely_binary(path, sample_output):
            return ReadResult(is_binary=True, file_size=file_size, error="Binary file — cannot display as text.")
        cat_result = self._exec(f"cat {self._escape_shell_arg(path)}")
        if cat_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {cat_result.stdout}")
        raw_content, _ = _strip_bom(_strip_terminal_fence_leaks(cat_result.stdout))
        return ReadResult(content=raw_content, file_size=file_size)

    def delete_file(self, path: str) -> WriteResult:
        """删除单个文件。"""
        return self._python_delete(path, recursive=False)

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        """跨平台删除：文件直接删，目录需 ``recursive=True`` 才删子树。"""
        return self._python_delete(path, recursive=recursive)

    def _python_delete(self, path: str, recursive: bool) -> WriteResult:
        path = self._expand_path(path)
        if is_write_denied(path):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        snippet = (
            "import shutil, pathlib, sys\n"
            f"p = pathlib.Path({path!r})\n"
            f"recursive = {bool(recursive)!r}\n"
            "try:\n"
            "    if p.is_dir() and not p.is_symlink():\n"
            "        if recursive:\n"
            "            shutil.rmtree(p)\n"
            "        else:\n"
            "            print('is a directory: ' + str(p), file=sys.stderr); sys.exit(2)\n"
            "    else:\n"
            "        p.unlink()\n"
            "except FileNotFoundError:\n"
            "    pass\n"
            "except Exception as exc:\n"
            "    print(str(exc), file=sys.stderr); sys.exit(1)\n"
        )
        result = self._exec(f"python3 -c {self._escape_shell_arg(snippet)}")
        if result.exit_code != 0 and "python3" in (result.stdout or ""):
            result = self._exec(f"python -c {self._escape_shell_arg(snippet)}")
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to delete {path}: {(result.stdout or '').strip() or 'unknown error'}")
        return WriteResult()

    def move_file(self, src: str, dst: str) -> WriteResult:
        """通过 mv 移动文件。"""
        src = self._expand_path(src)
        dst = self._expand_path(dst)
        for p in (src, dst):
            if is_write_denied(p):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        result = self._exec(f"mv {self._escape_shell_arg(src)} {self._escape_shell_arg(dst)}")
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to move {src} -> {dst}: {result.stdout}")
        return WriteResult()

    def write_file(self, path: str, content: str) -> WriteResult:
        """把内容写入文件，必要时创建父目录。"""
        path = self._expand_path(path)
        if is_write_denied(path):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        # 拒绝目录 — 不然 atomic_write 的 mv 会把临时文件搬进目录里、字节数为 0、模型以为写成功。
        if self._exec(f"test -d {self._escape_shell_arg(path)}").exit_code == 0:
            return WriteResult(error=f"Path is a directory: '{path}'. Use a file path, not a directory.")
        ext = os.path.splitext(path)[1].lower()
        pre_content: str | None = None
        want_pre = ext in LINTERS_INPROC
        if want_pre:
            read_cmd = f"cat {self._escape_shell_arg(path)} 2>/dev/null"
            read_result = self._exec(read_cmd)
            if read_result.exit_code == 0 and read_result.stdout:
                pre_content = read_result.stdout
        original_ending = self._detect_file_line_ending(path, pre_content)
        if original_ending == "\r\n":
            content = _normalize_line_endings(content, "\r\n")
        if self._file_has_bom(path, pre_content) and not _has_bom(content):
            content = _UTF8_BOM + content
        parent = os.path.dirname(path)
        dirs_created = False
        if parent:
            mkdir_cmd = f"mkdir -p {self._escape_shell_arg(parent)}"
            mkdir_result = self._exec(mkdir_cmd)
            if mkdir_result.exit_code == 0:
                dirs_created = True
        write_result = self._atomic_write(path, content)
        if write_result.exit_code != 0:
            return WriteResult(error=f"Failed to write file: {write_result.stdout}")
        stat_cmd = f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null"
        stat_result = self._exec(stat_cmd)
        try:
            bytes_written = int(stat_result.stdout.strip())
        except ValueError:
            bytes_written = len(content.encode("utf-8"))
        lint_result = self._check_lint_delta(path, pre_content=pre_content, post_content=content)
        return WriteResult(
            bytes_written=bytes_written,
            dirs_created=dirs_created,
            lint=lint_result.to_dict() if lint_result else None,
        )

    def patch_replace(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> PatchResult:
        """用模糊匹配替换文件中的文本。"""
        path = self._expand_path(path)
        if is_write_denied(path):
            return PatchResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        read_cmd = f"cat {self._escape_shell_arg(path)} 2>/dev/null"
        read_result = self._exec(read_cmd)
        if read_result.exit_code != 0:
            return PatchResult(error=f"Failed to read file: {path}")
        content = read_result.stdout
        content, _ = _strip_bom(content)
        new_content, match_count, _strategy, error = fuzzy_find_and_replace(
            content,
            old_string,
            new_string,
            replace_all,
        )
        if error or match_count == 0:
            err_msg = error or f"Could not find match for old_string in {path}"
            with suppress(Exception):
                err_msg += format_no_match_hint(err_msg, match_count, old_string, content)
            return PatchResult(error=err_msg)
        file_ending = _detect_line_ending(content)
        if file_ending:
            new_content = _normalize_line_endings(new_content, file_ending)
        write_result = self.write_file(path, new_content)
        if write_result.error:
            return PatchResult(error=f"Failed to write changes: {write_result.error}")
        verify_cmd = f"cat {self._escape_shell_arg(path)} 2>/dev/null"
        verify_result = self._exec(verify_cmd)
        if verify_result.exit_code != 0:
            return PatchResult(error=f"Post-write verification failed: could not re-read {path}")
        _verify_bomless, _ = _strip_bom(verify_result.stdout)
        _verify_stdout_normalized = _verify_bomless.replace("\r\n", "\n").replace("\r", "\n")
        _new_content_normalized = new_content.replace("\r\n", "\n").replace("\r", "\n")
        if _verify_stdout_normalized != _new_content_normalized:
            return PatchResult(
                error=(
                    f"Post-write verification failed for {path}: on-disk content "
                    f"differs from intended write "
                    f"(wrote {len(_new_content_normalized)} chars, read back "
                    f"{len(_verify_stdout_normalized)} chars after normalizing line endings). "
                    "The patch did not persist. Re-read the file and try again."
                ),
            )
        diff = self._unified_diff(content, new_content, path)
        lint_result = self._check_lint_delta(path, pre_content=content, post_content=new_content)
        return PatchResult(
            success=True,
            diff=diff,
            files_modified=[path],
            lint=lint_result.to_dict() if lint_result else None,
        )

    def patch_v4a(self, patch_content: str) -> PatchResult:
        """应用 V4A 格式补丁。"""
        operations, parse_error = parse_v4a_patch(patch_content)
        if parse_error:
            return PatchResult(error=f"Failed to parse patch: {parse_error}")
        result = apply_v4a_operations(operations, self)
        return result

    def _check_lint(self, path: str, content: str | None = None) -> LintResult:
        """编辑后对文件执行语法检查。"""
        ext = os.path.splitext(path)[1].lower()
        inproc = LINTERS_INPROC.get(ext)
        if inproc is not None:
            if content is None:
                read_cmd = f"cat {self._escape_shell_arg(path)} 2>/dev/null"
                read_result = self._exec(read_cmd)
                if read_result.exit_code != 0:
                    return LintResult(skipped=True, message=f"Failed to read {path} for lint")
                content = read_result.stdout
            ok, err = inproc(content)
            return LintResult(success=ok, output="" if ok else err)
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")
        linter_cmd = LINTERS[ext]
        base_cmd = linter_cmd.split()[0]
        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")
        cmd = linter_cmd.replace("{file}", self._escape_shell_arg(path))
        result = self._exec(cmd, timeout=30)
        if result.exit_code != 0 and _looks_like_linter_unusable(base_cmd, result.stdout):
            cleaned = strip_ansi(result.stdout).strip()
            first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), cleaned[:120])
            return LintResult(skipped=True, message=f"{base_cmd} not usable: {first_line[:200]}")
        return LintResult(success=result.exit_code == 0, output=result.stdout.strip() if result.stdout.strip() else "")

    def _check_lint_delta(self, path: str, pre_content: str | None, post_content: str | None = None) -> LintResult:
        """写后语法 lint，与写前基线对比，仅报告新增错误。"""
        post = self._check_lint(path, content=post_content)
        if post.success or post.skipped:
            return post
        if pre_content is None:
            return post
        pre = self._check_lint(path, content=pre_content)
        if pre.success or pre.skipped or not pre.output:
            return post
        pre_lines = {ln.strip() for ln in pre.output.splitlines() if ln.strip()}
        post_lines = [ln for ln in post.output.splitlines() if ln.strip() and ln.strip() not in pre_lines]
        if not post_lines:
            return LintResult(
                success=False,
                output=post.output,
                message="Pre-existing lint errors — this edit didn't introduce new ones but the file is still broken.",
            )
        return LintResult(
            success=False,
            output=(
                "New lint errors introduced by this edit (pre-existing errors filtered out):\n" + "\n".join(post_lines)
            ),
        )

    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = 50,
        offset: int = 0,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult:
        """搜索内容或文件名。"""
        offset, limit = normalize_search_pagination(offset, limit)
        path = self._expand_path(path)
        check = self._exec(f"test -e {self._escape_shell_arg(path)} && echo exists || echo not_found")
        if "not_found" in check.stdout:
            parent = os.path.dirname(path) or "."
            basename_query = os.path.basename(path)
            hint_parts = [f"Path not found: {path}"]
            parent_check = self._exec(f"test -d {self._escape_shell_arg(parent)} && echo yes || echo no")
            if "yes" in parent_check.stdout and basename_query:
                ls_result = self._exec(f"ls -1 {self._escape_shell_arg(parent)} 2>/dev/null | head -20")
                if ls_result.exit_code == 0 and ls_result.stdout.strip():
                    lower_q = basename_query.lower()
                    candidates = []
                    for entry in ls_result.stdout.strip().split("\n"):
                        if not entry:
                            continue
                        le = entry.lower()
                        if lower_q in le or le in lower_q or le.startswith(lower_q[:3]):
                            candidates.append(os.path.join(parent, entry))
                    if candidates:
                        hint_parts.append("Similar paths: " + ", ".join(candidates[:5]))
            return SearchResult(error=". ".join(hint_parts), total_count=0)
        if target == "files":
            return self._search_files(pattern, path, limit, offset)
        else:
            return self._search_content(pattern, path, file_glob, limit, offset, output_mode, context)

    def _search_files(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """按 glob 模式按文件名搜索。"""
        search_pattern = pattern if not pattern.startswith("**/") and "/" not in pattern else pattern.rsplit("/", 1)[-1]
        search_root = Path(path)
        has_hidden_path_ancestor = any(part not in {".", ".."} and part.startswith(".") for part in search_root.parts)
        if self._has_command("rg"):
            return self._search_files_rg(search_pattern, path, limit, offset)
        if not self._has_command("find"):
            return SearchResult(
                error="File search requires 'rg' (ripgrep) or 'find'. Install ripgrep for best results: https://github.com/BurntSushi/ripgrep#installation",
            )
        hidden_exclude = "-not -path '*/.*'" if not has_hidden_path_ancestor else ""
        hidden_filter_expr = f" {hidden_exclude}" if hidden_exclude else ""
        pagination_expr = ""
        if not has_hidden_path_ancestor:
            pagination_expr = f" | tail -n +{offset + 1} | head -n {limit}"
        cmd = (
            f"find {self._escape_shell_arg(path)}{hidden_filter_expr} -type f -name {self._escape_shell_arg(search_pattern)} "
            f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn{pagination_expr}"
        )
        result = self._exec(cmd, timeout=60)
        if not result.stdout.strip():
            cmd_simple = f"find {self._escape_shell_arg(path)}{hidden_filter_expr} -type f -name {self._escape_shell_arg(search_pattern)} 2>/dev/null | sort -rn{pagination_expr}"
            result = self._exec(cmd_simple, timeout=60)
        files = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0].replace(".", "").isdigit():
                files.append(parts[1])
            else:
                files.append(line)
        if has_hidden_path_ancestor:
            normalized_root = search_root.resolve()
            filtered_files = []
            for file_path in files:
                try:
                    rel_parts = Path(file_path).resolve().relative_to(normalized_root).parts
                except ValueError:
                    rel_parts = Path(file_path).parts
                if any(part not in {".", ".."} and part.startswith(".") for part in rel_parts):
                    continue
                filtered_files.append(file_path)
            files = filtered_files[offset : offset + limit]
        return SearchResult(files=files, total_count=len(files))

    def _search_files_rg(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """用 ripgrep 的 --files 模式按文件名搜索。"""
        glob_pattern = f"*{pattern}" if "/" not in pattern and not pattern.startswith("*") else pattern
        # 多取一条以让「恰好 limit 条」不被错判为截断（仅 >= 检查无法区分）。
        fetch_limit = limit + offset + 1
        cmd_sorted = f"rg --files --sortr=modified -g {self._escape_shell_arg(glob_pattern)} {self._escape_shell_arg(path)} 2>/dev/null | head -n {fetch_limit}"
        result = self._exec(cmd_sorted, timeout=60)
        all_files = [f for f in result.stdout.strip().split("\n") if f]
        if not all_files:
            cmd_plain = f"rg --files -g {self._escape_shell_arg(glob_pattern)} {self._escape_shell_arg(path)} 2>/dev/null | head -n {fetch_limit}"
            result = self._exec(cmd_plain, timeout=60)
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        truncated = len(all_files) > limit + offset
        total = min(len(all_files), limit + offset) if truncated else len(all_files)
        page = all_files[offset : offset + limit]
        return SearchResult(files=page, total_count=total, truncated=truncated)

    def _search_content(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        """在文件内搜索内容（类似 grep）。"""
        if self._has_command("rg"):
            return self._search_with_rg(pattern, path, file_glob, limit, offset, output_mode, context)
        elif self._has_command("grep"):
            return self._search_with_grep(pattern, path, file_glob, limit, offset, output_mode, context)
        else:
            return SearchResult(
                error="Content search requires ripgrep (rg) or grep. Install ripgrep: https://github.com/BurntSushi/ripgrep#installation",
            )

    def _search_with_rg(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        """使用 ripgrep 搜索。"""
        cmd_parts = ["rg", "--line-number", "--no-heading", "--with-filename"]
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--glob", self._escape_shell_arg(file_glob)])
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        fetch_limit = limit + offset + 200 if context > 0 else limit + offset
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        cmd = "set -o pipefail; " + " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        diagnostics, payload = _split_tool_diagnostics(result.stdout)
        if result.exit_code == 2 and not payload.strip():
            error_msg = diagnostics.strip() or result.stdout.strip() or "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)
        stdout = payload
        if output_mode == "files_only":
            all_files = [f for f in stdout.strip().split("\n") if f]
            total = len(all_files)
            page = all_files[offset : offset + limit]
            return SearchResult(files=page, total_count=total)
        elif output_mode == "count":
            counts = {}
            for line in stdout.strip().split("\n"):
                if ":" in line:
                    parts = line.rsplit(":", 1)
                    if len(parts) == 2:
                        with suppress(ValueError):
                            counts[parts[0]] = int(parts[1])
            return SearchResult(counts=counts, total_count=sum(counts.values()))
        else:
            _match_re = _SEARCH_LINE_RE
            matches = []
            for line in stdout.strip().split("\n"):
                if not line or line == "--":
                    continue
                m = _match_re.match(line)
                if m:
                    matches.append(
                        SearchMatch(
                            path=(m.group(1) or "") + m.group(2),
                            line_number=int(m.group(3)),
                            content=m.group(4)[:500],
                        ),
                    )
                    continue
                if context > 0:
                    parsed = _parse_search_context_line(line)
                    if parsed:
                        matches.append(SearchMatch(path=parsed[0], line_number=parsed[1], content=parsed[2][:500]))
            total = len(matches)
            page = matches[offset : offset + limit]
            return SearchResult(matches=page, total_count=total, truncated=total > offset + limit)

    def _search_with_grep(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        """使用 grep 兜底搜索。"""
        cmd_parts = ["grep", "-rnH"]
        cmd_parts.append("--exclude-dir='.*'")
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--include", self._escape_shell_arg(file_glob)])
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        fetch_limit = limit + offset + (200 if context > 0 else 0)
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        cmd = "set -o pipefail; " + " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        diagnostics, payload = _split_tool_diagnostics(result.stdout)
        if result.exit_code == 2 and not payload.strip():
            error_msg = diagnostics.strip() or result.stdout.strip() or "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)
        stdout = payload
        if output_mode == "files_only":
            all_files = [f for f in stdout.strip().split("\n") if f]
            total = len(all_files)
            page = all_files[offset : offset + limit]
            return SearchResult(files=page, total_count=total)
        elif output_mode == "count":
            counts = {}
            for line in stdout.strip().split("\n"):
                if ":" in line:
                    parts = line.rsplit(":", 1)
                    if len(parts) == 2:
                        with suppress(ValueError):
                            counts[parts[0]] = int(parts[1])
            return SearchResult(counts=counts, total_count=sum(counts.values()))
        else:
            _match_re = _SEARCH_LINE_RE
            matches = []
            for line in stdout.strip().split("\n"):
                if not line or line == "--":
                    continue
                m = _match_re.match(line)
                if m:
                    matches.append(
                        SearchMatch(
                            path=(m.group(1) or "") + m.group(2),
                            line_number=int(m.group(3)),
                            content=m.group(4)[:500],
                        ),
                    )
                    continue
                if context > 0:
                    parsed = _parse_search_context_line(line)
                    if parsed:
                        matches.append(SearchMatch(path=parsed[0], line_number=parsed[1], content=parsed[2][:500]))
            total = len(matches)
            page = matches[offset : offset + limit]
            return SearchResult(matches=page, total_count=total, truncated=total > offset + limit)


# ── File State ─────────────────────────────────────────────────────────────

ReadStamp = tuple[float, float, bool]  # (mtime, ts, partial)
_MAX_PATHS_PER_AGENT = 4096
_MAX_GLOBAL_WRITERS = 4096


def _cap_dict(d: dict, limit: int) -> None:
    over = len(d) - limit
    if over <= 0:
        return
    for key in list(d)[:over]:
        d.pop(key, None)


def _disabled() -> bool:
    return bool(cfg_get(load_config(), "file_state", "disabled", default=False))


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _safe_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class FileStateRegistry:
    """跨 Agent 文件编辑的进程级协调器。"""

    def __init__(self) -> None:
        self._reads: dict[str, dict[str, ReadStamp]] = {}
        self._last_writer: dict[str, tuple[str, float]] = {}
        self._path_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def _lock_for(self, resolved: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._path_locks.get(resolved)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[resolved] = lock
            return lock

    @contextmanager
    def lock_path(self, resolved: str | Path) -> Iterator[None]:
        """read→modify→write 段的单路径锁。"""
        with self._lock_for(str(resolved)):
            yield

    def record_read(self, task_id: str, resolved: str | Path, *, partial: bool = False) -> None:
        if _disabled():
            return
        resolved_s = str(resolved)
        mtime = _safe_mtime(resolved_s)
        if mtime is None:
            return
        with self._state_lock:
            agent_reads = self._reads.setdefault(task_id, {})
            agent_reads[resolved_s] = (mtime, time.time(), partial)
            _cap_dict(agent_reads, _MAX_PATHS_PER_AGENT)

    def note_write(self, task_id: str, resolved: str | Path) -> None:
        """记录一次成功的写入。"""
        if _disabled():
            return
        resolved_s = str(resolved)
        mtime = _safe_mtime(resolved_s)
        if mtime is None:
            return
        now = time.time()
        with self._state_lock:
            self._last_writer[resolved_s] = (task_id, now)
            _cap_dict(self._last_writer, _MAX_GLOBAL_WRITERS)
            self._reads.setdefault(task_id, {})[resolved_s] = (mtime, now, False)
            _cap_dict(self._reads[task_id], _MAX_PATHS_PER_AGENT)

    def check_stale(self, task_id: str, resolved: str | Path) -> str | None:
        """若此处写入将基于过期状态则返回面向模型的警告。"""
        if _disabled():
            return None
        resolved_s = str(resolved)
        with self._state_lock:
            stamp = self._reads.get(task_id, {}).get(resolved_s)
            last_writer = self._last_writer.get(resolved_s)
        if stamp is None and last_writer is None:
            return None
        if (current_mtime := _safe_mtime(resolved_s)) is None:
            return None
        if last_writer:
            writer_tid, writer_ts = last_writer
            if writer_tid != task_id:
                if stamp is None:
                    return (
                        f"{resolved_s} was modified by sibling subagent "
                        f"{writer_tid!r} but this agent never read it. "
                        "Read the file before writing to avoid overwriting "
                        "the sibling's changes."
                    )
                if writer_ts > stamp[1]:
                    return (
                        f"{resolved_s} was modified by sibling subagent "
                        f"{writer_tid!r} at {_fmt_ts(writer_ts)} — after "
                        f"this agent's last read at {_fmt_ts(stamp[1])}. "
                        "Re-read the file before writing."
                    )
        if stamp is not None:
            if current_mtime != stamp[0]:
                return f"{resolved_s} was modified since you last read it on disk (external edit or unrecorded writer). Re-read the file before writing."
            if stamp[2]:
                return f"{resolved_s} was last read with offset/limit pagination (partial view). Re-read the whole file before overwriting it."
        if stamp is None:
            return f"{resolved_s} was not read by this agent. Read the file first so you can write an informed edit."
        return None

    def clear(self) -> None:
        """重置所有状态。"""
        with self._state_lock:
            self._reads.clear()
            self._last_writer.clear()
        with self._meta_lock:
            self._path_locks.clear()


_REGISTRY = FileStateRegistry()


def record_read(task_id: str, resolved_or_path: str | Path, *, partial: bool = False) -> None:
    _REGISTRY.record_read(task_id, resolved_or_path, partial=partial)


def note_write(task_id: str, resolved_or_path: str | Path) -> None:
    _REGISTRY.note_write(task_id, resolved_or_path)


def check_stale(task_id: str, resolved_or_path: str | Path) -> str | None:
    return _REGISTRY.check_stale(task_id, resolved_or_path)


def lock_path(resolved_or_path: str | Path) -> Any:
    return _REGISTRY.lock_path(resolved_or_path)


# ── Patch Parser ───────────────────────────────────────────────────────────


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass(slots=True)
class HunkLine:
    prefix: str  # "+", "-", or " "
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
    content: str | None = None


_HEADER_PATTERNS: tuple[tuple[re.Pattern, OperationType], ...] = (
    (re.compile(r"\*\*\*\s*Update\s+File:\s*(.+)"), OperationType.UPDATE),
    (re.compile(r"\*\*\*\s*Add\s+File:\s*(.+)"), OperationType.ADD),
    (re.compile(r"\*\*\*\s*Delete\s+File:\s*(.+)"), OperationType.DELETE),
    (re.compile(r"\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)"), OperationType.MOVE),
)


def _flush(op: PatchOperation | None, hunk: Hunk | None, sink: list[PatchOperation]) -> None:
    if not op:
        return
    if hunk and hunk.lines:
        op.hunks.append(hunk)
    sink.append(op)


def _hunk_text(hunk: Hunk, prefix_set: str) -> str:
    return "\n".join(line.content for line in hunk.lines if line.prefix in prefix_set)


def _hunk_to_search_replace(hunk: Hunk) -> tuple[str, str]:
    return _hunk_text(hunk, " -"), _hunk_text(hunk, " +")


def _add_hunk_line(hunk: Hunk, line: str) -> None:
    if line.startswith("\\"):
        return
    if line[0] in "+- ":
        hunk.lines.append(HunkLine(line[0], line[1:]))
    else:
        hunk.lines.append(HunkLine(" ", line))


def _hint_uniqueness_error(file_path: str, text: str, hint: str) -> str | None:
    n = text.count(hint)
    if n == 0:
        return f"{file_path}: addition-only hunk context hint '{hint}' not found"
    if n > 1:
        return f"{file_path}: addition-only hunk context hint '{hint}' is ambiguous ({n} occurrences)"
    return None


def _insert_at_hint(text: str, hint: str, insert: str) -> tuple[str, str | None]:
    """返回 (新文本, 错误)；两者必有且仅有一个非 None。"""
    occurrences = text.count(hint)
    if occurrences == 0:
        return text.rstrip("\n") + "\n" + insert + "\n", None
    if occurrences > 1:
        return None, (
            f"Addition-only hunk: context hint '{hint}' is ambiguous ({occurrences} occurrences) — provide a more unique hint"
        )
    pos = text.find(hint)
    eol = text.find("\n", pos)
    if eol == -1:
        return text + "\n" + insert, None
    return text[: eol + 1] + insert + "\n" + text[eol + 1 :], None


def parse_v4a_patch(patch_content: str) -> tuple[list[PatchOperation], str | None]:
    lines = patch_content.split("\n")
    operations: list[PatchOperation] = []
    start_idx, end_idx = -1, len(lines)
    for i, line in enumerate(lines):
        if start_idx == -1 and ("*** Begin Patch" in line or "***Begin Patch" in line):
            start_idx = i
        elif "*** End Patch" in line or "***End Patch" in line:
            end_idx = i
            break
    current_op: PatchOperation | None = None
    current_hunk: Hunk | None = None
    i = start_idx + 1
    while i < end_idx:
        line = lines[i]
        matched_op: OperationType | None = None
        for pat, op_type in _HEADER_PATTERNS:
            if m := pat.match(line):
                _flush(current_op, current_hunk, operations)
                if op_type is OperationType.MOVE:
                    current_op = PatchOperation(
                        operation=op_type,
                        file_path=m.group(1).strip(),
                        new_path=m.group(2).strip(),
                    )
                    _flush(current_op, current_hunk, operations)
                    current_op = current_hunk = None
                elif op_type is OperationType.ADD:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    current_hunk = Hunk()
                elif op_type is OperationType.DELETE:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    _flush(current_op, current_hunk, operations)
                    current_op = current_hunk = None
                else:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    current_hunk = None
                matched_op = op_type
                break
        if matched_op is not None:
            i += 1
            continue
        if line.startswith("@@"):
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                hint = _HUNK_HINT_RE.match(line)
                current_hunk = Hunk(context_hint=hint.group(1) if hint else None)
        elif current_op and line:
            if current_hunk is None:
                current_hunk = Hunk()
            _add_hunk_line(current_hunk, line)
        i += 1
    _flush(current_op, current_hunk, operations)
    if not operations:
        return operations, None
    errors = [
        msg
        for op in operations
        for msg in (
            "Operation with empty file path" if not op.file_path else "",
            f"UPDATE {op.file_path!r}: no hunks found" if op.operation is OperationType.UPDATE and not op.hunks else "",
            f"MOVE {op.file_path!r}: missing destination path (expected 'src -> dst')"
            if op.operation is OperationType.MOVE and not op.new_path
            else "",
        )
        if msg
    ]
    if errors:
        return [], "Parse error: " + "; ".join(errors)
    return operations, None


def _retry_windowed(content: str, search: str, replace: str, hint: str) -> tuple[str, str | None]:
    """以 hint 为中心的小窗口内重试模糊匹配。"""
    pos = content.find(hint)
    if pos == -1:
        return content, "context hint not found"
    start = max(0, pos - 500)
    end = min(len(content), pos + 2000)
    window_new, wcount, _, werror = fuzzy_find_and_replace(content[start:end], search, replace, replace_all=False)
    if wcount > 0:
        return content[:start] + window_new + content[end:], None
    return content, werror


def _apply_addition_only(content: str, hunk: Hunk) -> tuple[str, str | None]:
    """在 hint 处插入 hunk 的 ``+`` 行（或追加到末尾）。"""
    insert_text = _hunk_text(hunk, " +")
    if hunk.context_hint:
        return _insert_at_hint(content, hunk.context_hint, insert_text)
    return content.rstrip("\n") + "\n" + insert_text + "\n", None


def _validate_operations(operations: list[PatchOperation], file_ops: Any) -> list[str]:
    """模拟跑一遍，返回错误信息列表。"""
    errors: list[str] = []
    for op in operations:
        if op.operation is OperationType.UPDATE:
            read_result = file_ops.read_file_raw(op.file_path)
            if read_result.error:
                errors.append(f"{op.file_path}: {read_result.error}")
                continue
            simulated = read_result.content
            for hunk in op.hunks:
                search, replace = _hunk_to_search_replace(hunk)
                if not search:
                    if hunk.context_hint and (
                        err := _hint_uniqueness_error(op.file_path, simulated, hunk.context_hint)
                    ):
                        errors.append(err)
                    continue
                new_simulated, count, _strategy, match_error = fuzzy_find_and_replace(
                    simulated,
                    search,
                    replace,
                    replace_all=False,
                )
                if count == 0:
                    label = f"'{hunk.context_hint}'" if hunk.context_hint else "(no hint)"
                    msg = f"{op.file_path}: hunk {label} not found" + (f" — {match_error}" if match_error else "")
                    with suppress(Exception):
                        msg += format_no_match_hint(match_error, count, search, simulated)
                    errors.append(msg)
                else:
                    simulated = new_simulated
        elif op.operation is OperationType.DELETE:
            if file_ops.read_file_raw(op.file_path).error:
                errors.append(f"{op.file_path}: file not found for deletion")
        elif op.operation is OperationType.MOVE:
            if not op.new_path:
                errors.append(f"{op.file_path}: MOVE operation missing destination path")
                continue
            if file_ops.read_file_raw(op.file_path).error:
                errors.append(f"{op.file_path}: source file not found for move")
            if not file_ops.read_file_raw(op.new_path).error:
                errors.append(f"{op.new_path}: destination already exists — move would overwrite")
    return errors


def _apply_add(op: PatchOperation, file_ops: Any) -> tuple[bool, str]:
    content = "\n".join(line.content for h in op.hunks for line in h.lines if line.prefix == "+")
    if (result := file_ops.write_file(op.file_path, content)).error:
        return False, result.error
    diff = f"--- /dev/null\n+++ b/{op.file_path}\n" + "\n".join(f"+{line}" for line in content.split("\n"))
    return True, diff


def _apply_delete(op: PatchOperation, file_ops: Any) -> tuple[bool, str]:
    read_result = file_ops.read_file_raw(op.file_path)
    if read_result.error:
        return False, f"Cannot delete {op.file_path}: file not found"
    if (result := file_ops.delete_file(op.file_path)).error:
        return False, result.error
    diff = "".join(
        difflib.unified_diff(
            read_result.content.splitlines(keepends=True),
            [],
            fromfile=f"a/{op.file_path}",
            tofile="/dev/null",
        ),
    )
    return True, diff or f"# Deleted: {op.file_path}"


def _apply_move(op: PatchOperation, file_ops: Any) -> tuple[bool, str]:
    if (result := file_ops.move_file(op.file_path, op.new_path)).error:
        return False, result.error
    return True, f"# Moved: {op.file_path} -> {op.new_path}"


def _apply_update(op: PatchOperation, file_ops: Any) -> tuple[bool, str]:
    read_result = file_ops.read_file_raw(op.file_path)
    if read_result.error:
        return False, f"Cannot read file: {read_result.error}"
    current_content = read_result.content
    new_content = current_content
    for hunk in op.hunks:
        search, replace = _hunk_to_search_replace(hunk)
        if search:
            new_content, count, _strategy, error = fuzzy_find_and_replace(
                new_content,
                search,
                replace,
                replace_all=False,
            )
            if error and count == 0 and hunk.context_hint:
                new_content, error = _retry_windowed(new_content, search, replace, hunk.context_hint)
            if error:
                err_msg = f"Could not apply hunk: {error}"
                with suppress(Exception):
                    err_msg += format_no_match_hint(error, 0, search, new_content)
                return False, err_msg
        else:
            new_content, err = _apply_addition_only(new_content, hunk)
            if err:
                return False, err
    if (write_result := file_ops.write_file(op.file_path, new_content)).error:
        return False, write_result.error
    diff = "".join(
        difflib.unified_diff(
            current_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{op.file_path}",
            tofile=f"b/{op.file_path}",
        ),
    )
    return True, diff


_APPLY: dict[OperationType, Any] = {
    OperationType.ADD: _apply_add,
    OperationType.UPDATE: _apply_update,
    OperationType.DELETE: _apply_delete,
    OperationType.MOVE: _apply_move,
}


def apply_v4a_operations(operations: list[PatchOperation], file_ops: Any) -> PatchResult:
    """两阶段：先校验再应用。"""
    if not operations:
        return PatchResult(
            success=False,
            error="Patch contained no operations (missing `*** Begin Patch` header or no file sections).",
        )
    errors = _validate_operations(operations, file_ops)
    if errors:
        return PatchResult(
            success=False,
            error="Patch validation failed (no files were modified):\n" + "\n".join(f"  • {e}" for e in errors),
        )
    files_modified: list[str] = []
    files_created: list[str] = []
    files_deleted: list[str] = []
    all_diffs: list[str] = []
    apply_errors: list[str] = []
    for op in operations:
        try:
            success, diff = _APPLY[op.operation](op, file_ops)
        except Exception as e:
            apply_errors.append(f"Error processing {op.file_path}: {e}")
            continue
        if not success:
            apply_errors.append(f"Failed to {op.operation.value} {op.file_path}: {diff}")
            continue
        match op.operation:
            case OperationType.ADD:
                files_created.append(op.file_path)
            case OperationType.DELETE:
                files_deleted.append(op.file_path)
            case OperationType.MOVE:
                files_modified.append(f"{op.file_path} -> {op.new_path}")
            case OperationType.UPDATE:
                files_modified.append(op.file_path)
        all_diffs.append(diff)
    lint_results = {
        f: file_ops._check_lint(f).to_dict() for f in files_modified + files_created if hasattr(file_ops, "_check_lint")
    }
    base = {
        "diff": "\n".join(all_diffs),
        "files_modified": files_modified,
        "files_created": files_created,
        "files_deleted": files_deleted,
        "lint": lint_results or None,
    }
    if apply_errors:
        return PatchResult(
            success=False,
            error="Apply phase failed (state may be inconsistent — run `git diff` to assess):\n"
            + "\n".join(f"  • {e}" for e in apply_errors),
            **base,
        )
    return PatchResult(success=True, **base)
