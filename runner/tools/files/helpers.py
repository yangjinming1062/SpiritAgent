import ast
import difflib
import json
import os
import posixpath
import re
import threading
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import yaml
from utils import cfg_get, is_write_denied, load_config, strip_ansi

from ..tool_output_limits import get_max_line_length, get_max_lines
from .binary_extensions import has_binary_extension
from .fuzzy_match import format_no_match_hint, fuzzy_find_and_replace

_SEARCH_LINE_RE = re.compile(r"^([A-Za-z]:)?(.*?):(\d+):(.*)$")
_CONTEXT_DELIM_RE = re.compile(r"-(\d+)-")
_HUNK_HINT_RE = re.compile(r"@@\s*(.+?)\s*@@")
_UTF8_BOM = "﻿"
# 搜索结果单行内容上限，避免压缩/生成文件中的超长行撑满结果。
MAX_MATCH_CONTENT = 500
# 整读进内存编辑（patch 与 V4A）的文件大小上限；分页读取不受此限。
MAX_EDIT_BYTES = 10 * 1024 * 1024
BINARY_FILE_ERROR = "Binary file — read_file and patch only handle text."


def _not_utf8_error(path: str) -> str:
    return f"{path} is not valid UTF-8 text; it cannot be edited safely with this tool."


def too_large_to_edit_error(path: str, size: int) -> str:
    return (
        f"{path} is {size:,} bytes; files over {MAX_EDIT_BYTES // (1024 * 1024)} MB cannot be edited with this tool. "
        "Use the terminal tool instead."
    )


# ── 结果类型 ───────────────────────────────────────────────────────────────


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


# ── 文本工具函数 ───────────────────────────────────────────────────────────


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


def _strip_bom(text: str) -> tuple[str, bool]:
    """返回 (去 BOM 文本, 是否有 BOM)。"""
    if text and text.startswith(_UTF8_BOM):
        return text[len(_UTF8_BOM) :], True
    return text, False


def _has_bom(text: str | None) -> bool:
    """若 ``text`` 以 UTF-8 BOM 开头则返回 True。"""
    return bool(text) and text.startswith(_UTF8_BOM)


def _to_lf(text: str) -> tuple[str, str | None]:
    """返回 (换行统一为 LF 的文本, 原主要换行符)。

    模糊匹配与 diff 在 LF 文本上进行，写回时再恢复原换行符：行尾残留的 ``\r`` 会让按行匹配失败或落到错误位置，
    还会让 ``\r`` 反转义把源码里的字面转义改成真实回车。
    """
    return _normalize_line_endings(text, "\n"), _detect_line_ending(text)


def _restore_line_endings(text: str, ending: str | None) -> str:
    return _normalize_line_endings(text, ending) if ending else text


def match_existing_format(content: str, existing: str | None) -> str:
    """让写入内容沿用已有文件的 CRLF 换行与 UTF-8 BOM；``existing`` 为已有文件开头的原始文本。"""
    if not existing:
        return content
    if _detect_line_ending(existing) == "\r\n":
        content = _normalize_line_endings(content, "\r\n")
    if _has_bom(existing) and not _has_bom(content):
        content = _UTF8_BOM + content
    return content


def is_binary_content(path: str, sample: str) -> bool:
    """按后缀名或文件开头采样判断是否为二进制文件。"""
    if has_binary_extension(path):
        return True
    head = sample[:1000]
    if not head:
        return False
    if "\x00" in head:
        return True
    non_printable = sum(1 for c in head if ord(c) < 32 and c not in "\n\r\t")
    return non_printable / len(head) > 0.30


def build_read_result(text: str, offset: int, limit: int, total_lines: int, file_size: int) -> ReadResult:
    """把分页读到的原文整理为带行号的 ReadResult；超长行按配置截断。"""
    if offset == 1:
        text, _ = _strip_bom(text)
    text = text.removesuffix("\n")
    numbered: list[str] = []
    if text:
        max_len = get_max_line_length()
        for i, line in enumerate(text.split("\n"), start=offset):
            line = line.removesuffix("\r")
            if len(line) > max_len:
                line = line[:max_len] + "... [truncated]"
            numbered.append(f"{i}|{line}")
    end_line = offset + limit - 1
    truncated = total_lines > end_line
    return ReadResult(
        content="\n".join(numbered),
        total_lines=total_lines,
        file_size=file_size,
        truncated=truncated,
        hint=(
            f"Use offset={end_line + 1} to continue reading (showing {offset}-{end_line} of {total_lines} lines)"
            if truncated
            else None
        ),
    )


def rank_similar_names(names: list[str], filename: str) -> list[str]:
    """按与 ``filename`` 的相似度给目录项排序，返回最相近的至多 5 个名字。"""
    lower_name = filename.lower()
    base, ext = os.path.splitext(lower_name)
    scored: list[tuple[int, str]] = []
    for name in names:
        lf = name.lower()
        score = 0
        if lf == lower_name:
            score = 100
        elif os.path.splitext(lf)[0] == base:
            score = 90
        elif lf.startswith(lower_name) or lower_name.startswith(lf):
            score = 70
        elif lower_name in lf:
            score = 60
        elif lf in lower_name and len(lf) > 2:
            score = 40
        elif ext and os.path.splitext(lf)[1] == ext:
            common = set(lower_name) & set(lf)
            if len(common) >= max(len(lower_name), len(lf)) * 0.4:
                score = 30
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda x: -x[0])
    return [name for _, name in scored[:5]]


def _unified_diff(old_content: str, new_content: str, filename: str) -> str:
    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


# ── 语法检查 ───────────────────────────────────────────────────────────────

# 进程内检查器未覆盖的语言调用外部命令。
LINTERS = {
    ".js": "node --check {file} 2>&1",
    # --no-install：未装 TypeScript 时直接失败，不让 npx 临时从网络下载同名包。
    ".ts": "npx --no-install tsc --noEmit {file} 2>&1",
    ".go": "go vet {file} 2>&1",
    ".rs": "rustfmt --check {file} 2>&1",
}

_LINTER_UNUSABLE_PATTERNS = {
    "npx": (
        "this is not the tsc command you are looking for",
        "could not determine executable to run",
        "not found in npm registry",
        "canceled due to missing packages",
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


LINTERS_INPROC: dict[str, Callable[[str], tuple[bool, str]]] = {
    ".py": _lint_python_inproc,
    ".json": _lint_json_inproc,
    ".yaml": _lint_yaml_inproc,
    ".yml": _lint_yaml_inproc,
    ".toml": _lint_toml_inproc,
}

# ── 分页 ───────────────────────────────────────────────────────────────────

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
    normalized_offset = max(1, _coerce_int(offset, DEFAULT_READ_OFFSET))
    normalized_limit = max(1, min(_coerce_int(limit, DEFAULT_READ_LIMIT), get_max_lines()))
    return normalized_offset, normalized_limit


def normalize_search_pagination(
    offset: Any = DEFAULT_SEARCH_OFFSET,
    limit: Any = DEFAULT_SEARCH_LIMIT,
) -> tuple[int, int]:
    """返回 shell head/tail 管线安全的搜索分页边界。"""
    normalized_offset = max(0, _coerce_int(offset, DEFAULT_SEARCH_OFFSET))
    normalized_limit = max(1, _coerce_int(limit, DEFAULT_SEARCH_LIMIT))
    return normalized_offset, normalized_limit


# ── 文件操作接口 ───────────────────────────────────────────────────────────


class TerminalEnv(Protocol):
    """文件操作依赖的终端环境能力；``cwd`` 随终端命令实时更新。"""

    cwd: str
    env_type: str

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]: ...


class FileOperations(ABC):
    """文件工具在终端环境上的操作接口。

    子类提供读写、删除、移动、搜索与命令执行原语；替换补丁与语法检查在此统一实现，本地与远端行为一致。
    """

    def __init__(self, env: TerminalEnv) -> None:
        self.env = env

    @abstractmethod
    def read_file(self, path: str, offset: int = DEFAULT_READ_OFFSET, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        """按行分页读取文本并加行号。"""

    @abstractmethod
    def read_file_raw(self, path: str) -> ReadResult:
        """读取完整 UTF-8 文本供编辑：保留原换行符、去掉 BOM；二进制或非 UTF-8 时返回错误。"""

    @abstractmethod
    def write_file(self, path: str, content: str) -> WriteResult:
        """原子写入；沿用已有文件的 CRLF 与 BOM，并对比写前内容做语法检查。"""

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def delete_file(self, path: str) -> WriteResult: ...

    @abstractmethod
    def move_file(self, src: str, dst: str) -> WriteResult: ...

    @abstractmethod
    def list_directory(self, path: str) -> ListResult: ...

    @abstractmethod
    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = DEFAULT_SEARCH_OFFSET,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult: ...

    @abstractmethod
    def _exec(self, command: str, timeout: int | None = None) -> ExecuteResult: ...

    @abstractmethod
    def _has_command(self, cmd: str) -> bool: ...

    @abstractmethod
    def _escape_shell_arg(self, arg: str) -> str: ...

    def patch_replace(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> PatchResult:
        """用模糊匹配替换文本；结果沿用文件原换行符，经 write_file 原子写入。"""
        read = self.read_file_raw(path)
        if read.error:
            return PatchResult(error=read.error)
        content, ending = _to_lf(read.content)
        old_string, new_string = _to_lf(old_string)[0], _to_lf(new_string)[0]
        new_content, _count, _strategy, error = fuzzy_find_and_replace(content, old_string, new_string, replace_all)
        if error is not None:
            return PatchResult(error=error + format_no_match_hint(error, 0, old_string, content))
        written = self.write_file(path, _restore_line_endings(new_content, ending))
        if written.error:
            return PatchResult(error=f"Failed to write changes: {written.error}")
        return PatchResult(
            success=True,
            diff=_unified_diff(content, new_content, path),
            files_modified=[path],
            lint=written.lint,
        )

    def _check_lint(self, path: str, content: str) -> LintResult:
        """语法检查：已知格式在进程内校验 ``content``，其他语言调用外部检查器检查磁盘上的文件。"""
        ext = os.path.splitext(path)[1].lower()
        if (inproc := LINTERS_INPROC.get(ext)) is not None:
            ok, err = inproc(_strip_bom(content)[0])
            return LintResult(success=ok, output="" if ok else err)
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")
        linter_cmd = LINTERS[ext]
        base_cmd = linter_cmd.split()[0]
        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")
        result = self._exec(linter_cmd.replace("{file}", self._escape_shell_arg(path)), timeout=30)
        if result.exit_code != 0 and _looks_like_linter_unusable(base_cmd, result.stdout):
            cleaned = strip_ansi(result.stdout).strip()
            first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), cleaned[:120])
            return LintResult(skipped=True, message=f"{base_cmd} not usable: {first_line[:200]}")
        return LintResult(success=result.exit_code == 0, output=result.stdout.strip())

    def _check_lint_delta(self, path: str, pre_content: str | None, post_content: str) -> LintResult:
        """写后语法检查；进程内检查器能拿写前内容做基线时，只报告本次新增的错误。"""
        post = self._check_lint(path, post_content)
        inproc = LINTERS_INPROC.get(os.path.splitext(path)[1].lower())
        if post.success or post.skipped or pre_content is None or inproc is None:
            return post
        pre_ok, pre_output = inproc(_strip_bom(pre_content)[0])
        if pre_ok or not pre_output:
            return post
        pre_lines = {ln.strip() for ln in pre_output.splitlines() if ln.strip()}
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


# ── 远端（SSH）实现 ────────────────────────────────────────────────────────


def _split_tool_diagnostics(output: str) -> tuple[str, str]:
    """把 rg/grep 自身的诊断行（stderr 合并进输出）与匹配结果分开。"""
    diagnostics: list[str] = []
    payload: list[str] = []
    for line in output.split("\n"):
        if not line.strip():
            continue
        stripped = line.lstrip()
        (diagnostics if stripped.startswith(("rg: ", "grep: ")) else payload).append(line)
    return "\n".join(diagnostics), "\n".join(payload)


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


def _parse_search_output(stdout: str, output_mode: str, limit: int, offset: int, context: int) -> SearchResult:
    """把 rg/grep 输出解析为分页后的 SearchResult。"""
    lines = [ln for ln in stdout.split("\n") if ln]
    if output_mode == "files_only":
        return SearchResult(
            files=lines[offset : offset + limit],
            total_count=len(lines),
            truncated=len(lines) > offset + limit,
        )
    if output_mode == "count":
        counts: dict[str, int] = {}
        for line in lines:
            name, sep, n = line.rpartition(":")
            if sep and n.isdigit():
                counts[name] = int(n)
        return SearchResult(counts=counts, total_count=sum(counts.values()))
    matches: list[SearchMatch] = []
    for line in lines:
        if line == "--":
            continue
        if m := _SEARCH_LINE_RE.match(line):
            matches.append(
                SearchMatch(
                    path=(m.group(1) or "") + m.group(2),
                    line_number=int(m.group(3)),
                    content=m.group(4)[:MAX_MATCH_CONTENT],
                ),
            )
        elif context > 0 and (parsed := _parse_search_context_line(line)):
            matches.append(SearchMatch(path=parsed[0], line_number=parsed[1], content=parsed[2][:MAX_MATCH_CONTENT]))
    return SearchResult(
        matches=matches[offset : offset + limit],
        total_count=len(matches),
        truncated=len(matches) > offset + limit,
    )


class ShellFileOperations(FileOperations):
    """远端环境：经终端环境执行 shell 命令，路径由远端 shell 按其当前目录解析。"""

    def __init__(self, env: TerminalEnv) -> None:
        super().__init__(env)
        self._command_cache: dict[str, bool] = {}

    def _exec(self, command: str, timeout: int | None = None, stdin_data: str | None = None) -> ExecuteResult:
        result = self.env.execute(command, timeout=timeout, stdin_data=stdin_data)
        return ExecuteResult(stdout=result.get("output", ""), exit_code=result.get("returncode", 0))

    def _has_command(self, cmd: str) -> bool:
        """检查命令是否在环境中可用（带缓存）。"""
        if cmd not in self._command_cache:
            result = self._exec(f"command -v {cmd} >/dev/null 2>&1 && echo 'yes'")
            self._command_cache[cmd] = result.stdout.strip() == "yes"
        return self._command_cache[cmd]

    def _escape_shell_arg(self, arg: str) -> str:
        """把字符串转义为 shell 命令可安全使用的形式。"""
        return "'" + arg.replace("'", "'\"'\"'") + "'"

    def _expand_path(self, path: str) -> str:
        """把 ``~``/``~user`` 形式的路径按远端 HOME 展开。"""
        if not path.startswith("~"):
            return path
        result = self._exec("echo $HOME")
        home = result.stdout.strip()
        if result.exit_code != 0 or not home:
            return path
        if path == "~":
            return home
        if path.startswith("~/"):
            return home + path[1:]
        rest = path[1:]
        slash_idx = rest.find("/")
        username = rest[:slash_idx] if slash_idx >= 0 else rest
        if username and re.fullmatch(r"[a-zA-Z0-9._-]+", username):
            expand_result = self._exec(f"echo ~{username}")
            if expand_result.exit_code == 0 and (user_home := expand_result.stdout.strip()):
                return user_home + path[1 + len(username) :]
        return path

    def _atomic_write(self, path: str, content: str) -> ExecuteResult:
        """同目录临时文件 + rename 原子写入；落盘字节数不符时不替换目标。

        缺父目录时创建并输出 ``__SPIRITAGENT_DIRS_CREATED__``；已有文件保留权限位，新文件按 umask。
        """
        q_path = self._escape_shell_arg(path)
        q_parent = self._escape_shell_arg(posixpath.dirname(path) or ".")
        tmpl = self._escape_shell_arg(".spiritagent-tmp.XXXXXX")
        expected = len(content.encode("utf-8"))
        # 第三兜底用 $RANDOM 不用 $$，避免并发写同目录的 PID 冲突。
        script = (
            "set -e; "
            f"d={q_parent}; t={q_path}; "
            'if [ ! -d "$d" ]; then mkdir -p "$d"; echo __SPIRITAGENT_DIRS_CREATED__; fi; '
            'tmp="$(mktemp -p "$d" ' + tmpl + " 2>/dev/null "
            '|| mktemp "$d/.spiritagent-tmp.$RANDOM.XXXXXX" 2>/dev/null '
            '|| { tmp="$d/.spiritagent-tmp.$RANDOM"; : > "$tmp" && echo "$tmp"; })"; '
            '[ -n "$tmp" ] || { echo "atomic write: could not create temp file" >&2; exit 1; }; '
            "trap 'rm -f \"$tmp\"' EXIT; "
            'if [ -e "$t" ]; then '
            'm="$(stat -c%a "$t" 2>/dev/null || stat -f%Lp "$t" 2>/dev/null || true)"; '
            "else "
            'm="$(printf %03o "$((0666 & ~0$(umask)))")"; '
            "fi; "
            '[ -n "$m" ] && chmod "$m" "$tmp" 2>/dev/null || true; '
            'cat > "$tmp"; '
            'n="$(wc -c < "$tmp" | tr -d " ")"; '
            f'[ "$n" = "{expected}" ] || {{ echo "atomic write: wrote $n of {expected} bytes" >&2; exit 1; }}; '
            'mv -f "$tmp" "$t"; '
            "trap - EXIT"
        )
        return self._exec(script, stdin_data=content)

    def _suggest_similar_files(self, path: str) -> ReadResult:
        """请求的文件未找到时推荐同目录下的相似文件。"""
        dir_path = posixpath.dirname(path) or "."
        ls_result = self._exec(f"ls -1 {self._escape_shell_arg(dir_path)} 2>/dev/null | head -50")
        names = [f for f in ls_result.stdout.strip().split("\n") if f] if ls_result.exit_code == 0 else []
        similar = [posixpath.join(dir_path, n) for n in rank_similar_names(names, posixpath.basename(path))]
        return ReadResult(error=f"File not found: {path}", similar_files=similar)

    def read_file(self, path: str, offset: int = DEFAULT_READ_OFFSET, limit: int = DEFAULT_READ_LIMIT) -> ReadResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        # awk 的 NR 计入无结尾换行的最后一行（wc -l 不计）。
        stat_result = self._exec(
            f"if [ -d {q} ]; then echo dir; elif [ -f {q} ]; then wc -c < {q}; awk 'END {{print NR}}' {q}; "
            "else exit 1; fi 2>/dev/null",
        )
        if stat_result.exit_code != 0:
            return self._suggest_similar_files(path)
        stat_lines = stat_result.stdout.split()
        if stat_lines == ["dir"]:
            return ReadResult(error=f"Path is a directory: '{path}'. Use list_directory instead.")
        try:
            file_size, total_lines = int(stat_lines[0]), int(stat_lines[1])
        except (IndexError, ValueError):
            return ReadResult(error=f"Failed to stat file: {stat_result.stdout.strip()}")
        sample = self._exec(f"head -c 1000 {q} 2>/dev/null").stdout
        if is_binary_content(path, sample):
            return ReadResult(is_binary=True, file_size=file_size, error=BINARY_FILE_ERROR)
        end_line = offset + limit - 1
        read_result = self._exec(f"sed -n '{offset},{end_line}p' {q}")
        if read_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {read_result.stdout}")
        return build_read_result(read_result.stdout, offset, limit, total_lines, file_size)

    def read_file_raw(self, path: str) -> ReadResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        size_result = self._exec(f"if [ -f {q} ]; then wc -c < {q}; else exit 1; fi 2>/dev/null")
        if size_result.exit_code != 0:
            return self._suggest_similar_files(path)
        try:
            file_size = int(size_result.stdout.strip())
        except ValueError:
            return ReadResult(error=f"Failed to stat file: {size_result.stdout.strip()}")
        if file_size > MAX_EDIT_BYTES:
            return ReadResult(file_size=file_size, error=too_large_to_edit_error(path, file_size))
        if is_binary_content(path, self._exec(f"head -c 1000 {q} 2>/dev/null").stdout):
            return ReadResult(is_binary=True, error=BINARY_FILE_ERROR)
        cat_result = self._exec(f"cat {q}")
        if cat_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {cat_result.stdout}")
        # 终端输出按 UTF-8 解码且以 U+FFFD 替换非法字节，原样写回会损坏非 UTF-8 文件。
        if "\ufffd" in cat_result.stdout:
            return ReadResult(error=_not_utf8_error(path))
        content, _ = _strip_bom(cat_result.stdout)
        return ReadResult(content=content, file_size=file_size)

    def write_file(self, path: str, content: str) -> WriteResult:
        path = self._expand_path(path)
        if is_write_denied(path):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")
        q = self._escape_shell_arg(path)
        probe = self._exec(f"if [ -d {q} ]; then echo dir; elif [ -f {q} ]; then wc -c < {q}; fi 2>/dev/null")
        kind = probe.stdout.strip()
        # 拒绝目录：否则 mv 会把临时文件搬进目录里，模型误以为写入成功。
        if kind == "dir":
            return WriteResult(error=f"Path is a directory: '{path}'. Use a file path, not a directory.")
        pre_content: str | None = None
        existing: str | None = None
        if kind.isdigit():
            # 进程内语法检查需要完整的写前内容做基线；其他类型或超大文件只取开头判断换行符与 BOM。
            full = os.path.splitext(path)[1].lower() in LINTERS_INPROC and int(kind) <= MAX_EDIT_BYTES
            head = self._exec(f"cat {q} 2>/dev/null" if full else f"head -c 4096 {q} 2>/dev/null")
            if head.exit_code == 0:
                existing = head.stdout
                pre_content = head.stdout if full else None
        content = match_existing_format(content, existing)
        write_result = self._atomic_write(path, content)
        if write_result.exit_code != 0:
            return WriteResult(error=f"Failed to write file: {write_result.stdout.strip()}")
        lint_result = self._check_lint_delta(path, pre_content, content)
        return WriteResult(
            bytes_written=len(content.encode("utf-8")),
            dirs_created="__SPIRITAGENT_DIRS_CREATED__" in write_result.stdout,
            lint=lint_result.to_dict(),
        )

    def exists(self, path: str) -> bool:
        return self._exec(f"test -e {self._escape_shell_arg(self._expand_path(path))}").exit_code == 0

    def delete_file(self, path: str) -> WriteResult:
        path = self._expand_path(path)
        if is_write_denied(path):
            return WriteResult(error=f"Delete denied: {path} is a protected path")
        q = self._escape_shell_arg(path)
        result = self._exec(f"if [ -d {q} ] && [ ! -L {q} ]; then echo 'is a directory' >&2; exit 2; fi; rm -f -- {q}")
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to delete {path}: {result.stdout.strip() or 'unknown error'}")
        return WriteResult()

    def move_file(self, src: str, dst: str) -> WriteResult:
        src = self._expand_path(src)
        dst = self._expand_path(dst)
        for p in (src, dst):
            if is_write_denied(p):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        q_dst = self._escape_shell_arg(dst)
        result = self._exec(
            f'mkdir -p -- "$(dirname -- {q_dst})" && mv -- {self._escape_shell_arg(src)} {q_dst}',
        )
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to move {src} -> {dst}: {result.stdout.strip()}")
        return WriteResult()

    def list_directory(self, path: str) -> ListResult:
        path = self._expand_path(path)
        q = self._escape_shell_arg(path)
        # GNU find 给出类型/大小/修改时间；BSD find 不支持 -printf 时只列名字。
        result = self._exec(
            f"if [ ! -e {q} ]; then echo __missing__; elif [ ! -d {q} ]; then echo __notdir__; "
            f"elif find {q} -maxdepth 0 -printf '' >/dev/null 2>&1; then "
            f"find {q} -mindepth 1 -maxdepth 1 -printf '%Y\\t%s\\t%T@\\t%f\\n' 2>/dev/null; "
            f"else ls -1Ap {q}; fi",
            timeout=60,
        )
        out = result.stdout.strip()
        if out == "__missing__":
            return ListResult(error=f"Directory '{path}' not found.")
        if out == "__notdir__":
            return ListResult(error=f"Path '{path}' is not a directory.")
        entries: list[dict[str, Any]] = []
        for line in out.split("\n"):
            if not line:
                continue
            parts = line.split("\t", 3)
            if len(parts) == 4:
                kind, size, mtime, name = parts
                is_dir = kind == "d"
                try:
                    entry_size, entry_mtime = int(size), float(mtime)
                except ValueError:
                    continue
                entries.append(
                    {
                        "name": name + ("/" if is_dir else ""),
                        "is_dir": is_dir,
                        "size": entry_size,
                        "mtime": entry_mtime,
                    },
                )
            else:
                entries.append({"name": line, "is_dir": line.endswith("/")})
        return ListResult(entries=entries)

    def search(
        self,
        pattern: str,
        path: str = ".",
        target: str = "content",
        file_glob: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = DEFAULT_SEARCH_OFFSET,
        output_mode: str = "content",
        context: int = 0,
    ) -> SearchResult:
        path = self._expand_path(path)
        if self._exec(f"test -e {self._escape_shell_arg(path)}").exit_code != 0:
            parent = posixpath.dirname(path) or "."
            hint = f"Path not found: {path}"
            ls_result = self._exec(f"ls -1 {self._escape_shell_arg(parent)} 2>/dev/null | head -50")
            names = [n for n in ls_result.stdout.strip().split("\n") if n] if ls_result.exit_code == 0 else []
            if similar := rank_similar_names(names, posixpath.basename(path)):
                hint += ". Similar paths: " + ", ".join(posixpath.join(parent, n) for n in similar)
            return SearchResult(error=hint)
        if target == "files":
            return self._search_files(pattern, path, limit, offset)
        if self._has_command("rg"):
            return self._search_content_rg(pattern, path, file_glob, limit, offset, output_mode, context)
        if self._has_command("grep"):
            return self._search_content_grep(pattern, path, file_glob, limit, offset, output_mode, context)
        return SearchResult(
            error="Content search requires ripgrep (rg) or grep in the terminal environment.",
        )

    def _search_files(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """按文件名 glob 搜索，按修改时间倒序；跳过搜索根下的隐藏路径。"""
        q_path = self._escape_shell_arg(path)
        # 多取一条以区分「恰好 limit 条」与截断。
        fetch = limit + offset + 1
        if self._has_command("rg"):
            glob_pattern = pattern if "/" in pattern or pattern.startswith("*") else f"*{pattern}"
            q_glob = self._escape_shell_arg(glob_pattern)
            result = self._exec(
                f"rg --files --sortr=modified -g {q_glob} {q_path} 2>/dev/null | head -n {fetch}",
                timeout=60,
            )
            if not result.stdout.strip():
                result = self._exec(f"rg --files -g {q_glob} {q_path} 2>/dev/null | head -n {fetch}", timeout=60)
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        elif self._has_command("find"):
            root = path.rstrip("/") or "/"
            hidden = " ".join(
                f"-not -path {self._escape_shell_arg(posixpath.join(root, sub))}" for sub in (".*", "*/.*")
            )
            # find -name 只比较文件名，取模式最后一段。
            name_pattern = self._escape_shell_arg(pattern.rsplit("/", 1)[-1])
            find_cmd = f"find {q_path} {hidden} -type f -name {name_pattern}"
            result = self._exec(
                f"{find_cmd} -printf '%T@ %p\\n' 2>/dev/null | sort -rn | cut -d' ' -f2- | head -n {fetch}",
                timeout=60,
            )
            if not result.stdout.strip():
                # BSD find 无 -printf：退化为不按修改时间排序。
                result = self._exec(f"{find_cmd} 2>/dev/null | head -n {fetch}", timeout=60)
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        else:
            return SearchResult(error="File search requires 'rg' (ripgrep) or 'find' in the terminal environment.")
        truncated = len(all_files) > limit + offset
        return SearchResult(
            files=all_files[offset : offset + limit],
            total_count=min(len(all_files), limit + offset),
            truncated=truncated,
        )

    def _search_content_rg(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        cmd_parts = ["rg", "--line-number", "--no-heading", "--with-filename"]
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--glob", self._escape_shell_arg(file_glob)])
        return self._run_content_search(cmd_parts, pattern, path, limit, offset, output_mode, context)

    def _search_content_grep(
        self,
        pattern: str,
        path: str,
        file_glob: str | None,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        # 跳过隐藏文件与隐藏目录；目录模式不能写成 '.*'，否则 GNU grep 会把命令行里的 "." 也排除。
        cmd_parts = ["grep", "-rnHE", "--exclude-dir='.[!.]*'", "--exclude-dir='..?*'", "--exclude='.*'"]
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        if file_glob:
            cmd_parts.extend(["--include", self._escape_shell_arg(file_glob)])
        return self._run_content_search(cmd_parts, pattern, path, limit, offset, output_mode, context)

    def _run_content_search(
        self,
        cmd_parts: list[str],
        pattern: str,
        path: str,
        limit: int,
        offset: int,
        output_mode: str,
        context: int,
    ) -> SearchResult:
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        # 模式以 - 开头时不能被当作选项。
        cmd_parts.extend(["-e", self._escape_shell_arg(pattern), "--", self._escape_shell_arg(path)])
        # 多取一行以判断截断；带上下文时每个匹配占多行，多留余量。
        fetch_limit = limit + offset + 1 + (200 if context > 0 else 0)
        result = self._exec(f"set -o pipefail; {' '.join(cmd_parts)} | head -n {fetch_limit}", timeout=60)
        diagnostics, payload = _split_tool_diagnostics(result.stdout)
        if result.exit_code == 2 and not payload.strip():
            return SearchResult(
                error=f"Search failed: {diagnostics.strip() or result.stdout.strip() or 'unknown error'}",
            )
        return _parse_search_output(payload, output_mode, limit, offset, context)


# ── 读写状态登记 ───────────────────────────────────────────────────────────

_MAX_PATHS_PER_TASK = 4096


def _disabled() -> bool:
    return bool(cfg_get(load_config(), "file_state", "disabled", default=False))


def _safe_mtime(path: str) -> float | None:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


class FileStateRegistry:
    """记录本机文件在各任务中最近一次读/写时的版本，写入前提示读后被外部修改或只读过局部。

    同一路径的读→改→写经 ``lock_path`` 串行化。
    """

    def __init__(self) -> None:
        self._reads: dict[str, dict[str, tuple[float, bool]]] = {}  # task_id → path → (mtime, 是否局部读取)
        self._path_locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self._state_lock = threading.Lock()

    @contextmanager
    def lock_path(self, resolved: str) -> Iterator[None]:
        with self._meta_lock:
            lock = self._path_locks.setdefault(resolved, threading.Lock())
        with lock:
            yield

    def _record(self, task_id: str, resolved: str, *, partial: bool) -> None:
        if _disabled() or (mtime := _safe_mtime(resolved)) is None:
            return
        with self._state_lock:
            reads = self._reads.setdefault(task_id, {})
            reads.pop(resolved, None)
            reads[resolved] = (mtime, partial)
            for key in list(reads)[: max(0, len(reads) - _MAX_PATHS_PER_TASK)]:
                del reads[key]

    def record_read(self, task_id: str, resolved: str, *, partial: bool) -> None:
        self._record(task_id, resolved, partial=partial)

    def note_write(self, task_id: str, resolved: str) -> None:
        self._record(task_id, resolved, partial=False)

    def check_stale(self, task_id: str, resolved: str, *, whole_file: bool) -> str | None:
        """返回面向模型的提示；``whole_file`` 为整文件覆盖时额外提示只读过局部。"""
        if _disabled():
            return None
        with self._state_lock:
            stamp = self._reads.get(task_id, {}).get(resolved)
        if stamp is None or (current_mtime := _safe_mtime(resolved)) is None:
            return None
        if current_mtime != stamp[0]:
            return (
                f"{resolved} changed on disk after your last read of it, so your view of the file may be outdated. "
                "Re-read it to verify the result."
            )
        if whole_file and stamp[1]:
            return (
                f"{resolved} was only partially read (offset/limit) before this full overwrite. "
                "Re-read it to verify nothing was lost."
            )
        return None


_REGISTRY = FileStateRegistry()


def record_read(task_id: str, resolved: str, *, partial: bool) -> None:
    _REGISTRY.record_read(task_id, resolved, partial=partial)


def note_write(task_id: str, resolved: str) -> None:
    _REGISTRY.note_write(task_id, resolved)


def check_stale(task_id: str, resolved: str, *, whole_file: bool) -> str | None:
    return _REGISTRY.check_stale(task_id, resolved, whole_file=whole_file)


def lock_path(resolved: str) -> AbstractContextManager[None]:
    return _REGISTRY.lock_path(resolved)


# ── V4A 补丁 ───────────────────────────────────────────────────────────────


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


@dataclass(slots=True)
class _OpOutcome:
    error: str | None = None
    diff: str = ""
    lint: dict[str, Any] | None = None


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


def _add_hunk_line(hunk: Hunk, line: str) -> None:
    if line.startswith("\\"):
        return
    if line[0] in "+- ":
        hunk.lines.append(HunkLine(line[0], line[1:]))
    else:
        hunk.lines.append(HunkLine(" ", line))


def parse_v4a_patch(patch_content: str) -> tuple[list[PatchOperation], str | None]:
    lines = patch_content.replace("\r\n", "\n").split("\n")
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
                    _flush(current_op, None, operations)
                    current_op = current_hunk = None
                elif op_type is OperationType.ADD:
                    current_op = PatchOperation(operation=op_type, file_path=m.group(1).strip())
                    current_hunk = Hunk()
                elif op_type is OperationType.DELETE:
                    _flush(PatchOperation(operation=op_type, file_path=m.group(1).strip()), None, operations)
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


def _insert_at_hint(text: str, hint: str, insert: str) -> tuple[str, str | None]:
    """在 hint 所在行之后插入；返回 (新文本, 错误)，出错时原文不变。"""
    occurrences = text.count(hint)
    if occurrences == 0:
        return text, f"addition-only hunk context hint '{hint}' not found"
    if occurrences > 1:
        return text, (
            f"addition-only hunk context hint '{hint}' is ambiguous ({occurrences} occurrences) — "
            "provide a more unique hint"
        )
    eol = text.find("\n", text.find(hint))
    if eol == -1:
        return text + "\n" + insert, None
    return text[: eol + 1] + insert + "\n" + text[eol + 1 :], None


def _retry_windowed(content: str, search: str, replace: str, hint: str) -> tuple[str, str | None]:
    """以 hint 为中心的小窗口内重试模糊匹配。"""
    pos = content.find(hint)
    if pos == -1:
        return content, "context hint not found"
    start = max(0, pos - 500)
    end = min(len(content), pos + 2000)
    window_new, _count, _, error = fuzzy_find_and_replace(content[start:end], search, replace, replace_all=False)
    if error is not None:
        return content, error
    return content[:start] + window_new + content[end:], None


def _apply_hunks(content: str, hunks: list[Hunk]) -> tuple[str, str | None]:
    """依次应用 hunk，返回 (新内容, 错误)；校验与实际写入共用，结果一致。"""
    for hunk in hunks:
        search, replace = _hunk_text(hunk, " -"), _hunk_text(hunk, " +")
        if not search:
            if hunk.context_hint:
                content, error = _insert_at_hint(content, hunk.context_hint, replace)
                if error is not None:
                    return content, error
            else:
                content = content.rstrip("\n") + "\n" + replace + "\n"
            continue
        new_content, _count, _strategy, error = fuzzy_find_and_replace(content, search, replace, replace_all=False)
        if error is not None and hunk.context_hint:
            new_content, error = _retry_windowed(content, search, replace, hunk.context_hint)
        if error is not None:
            label = f"'{hunk.context_hint}'" if hunk.context_hint else "(no hint)"
            return content, f"hunk {label} not found — {error}" + format_no_match_hint(error, 0, search, content)
        content = new_content
    return content, None


def _validate_operations(operations: list[PatchOperation], file_ops: FileOperations) -> list[str]:
    """不落盘地模拟整份补丁，返回全部错误。"""
    errors: list[str] = []
    for op in operations:
        match op.operation:
            case OperationType.UPDATE:
                read_result = file_ops.read_file_raw(op.file_path)
                if read_result.error:
                    errors.append(f"{op.file_path}: {read_result.error}")
                elif (error := _apply_hunks(_to_lf(read_result.content)[0], op.hunks)[1]) is not None:
                    errors.append(f"{op.file_path}: {error}")
            case OperationType.ADD:
                if file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: already exists — use '*** Update File:' to modify it")
            case OperationType.DELETE:
                if not file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: file not found for deletion")
            case OperationType.MOVE:
                if not file_ops.exists(op.file_path):
                    errors.append(f"{op.file_path}: source file not found for move")
                if op.new_path and file_ops.exists(op.new_path):
                    errors.append(f"{op.new_path}: destination already exists — move would overwrite")
    return errors


def _apply_add(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    lines = [line.content for h in op.hunks for line in h.lines if line.prefix == "+"]
    content = "\n".join(lines) + "\n" if lines else ""
    if (result := file_ops.write_file(op.file_path, content)).error:
        return _OpOutcome(error=result.error)
    diff = f"--- /dev/null\n+++ b/{op.file_path}\n" + "\n".join(f"+{line}" for line in lines)
    return _OpOutcome(diff=diff, lint=result.lint)


def _apply_delete(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    before = file_ops.read_file_raw(op.file_path)
    if (result := file_ops.delete_file(op.file_path)).error:
        return _OpOutcome(error=result.error)
    diff = "" if before.error else _unified_diff(before.content, "", op.file_path)
    return _OpOutcome(diff=diff or f"# Deleted: {op.file_path}")


def _apply_move(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    if op.new_path is None:
        return _OpOutcome(error="missing destination path")
    if (result := file_ops.move_file(op.file_path, op.new_path)).error:
        return _OpOutcome(error=result.error)
    return _OpOutcome(diff=f"# Moved: {op.file_path} -> {op.new_path}")


def _apply_update(op: PatchOperation, file_ops: FileOperations) -> _OpOutcome:
    read_result = file_ops.read_file_raw(op.file_path)
    if read_result.error:
        return _OpOutcome(error=f"Cannot read file: {read_result.error}")
    content, ending = _to_lf(read_result.content)
    new_content, error = _apply_hunks(content, op.hunks)
    if error is not None:
        return _OpOutcome(error=f"Could not apply {error}")
    if (write_result := file_ops.write_file(op.file_path, _restore_line_endings(new_content, ending))).error:
        return _OpOutcome(error=write_result.error)
    return _OpOutcome(
        diff=_unified_diff(content, new_content, op.file_path),
        lint=write_result.lint,
    )


_APPLY: dict[OperationType, Callable[[PatchOperation, FileOperations], _OpOutcome]] = {
    OperationType.ADD: _apply_add,
    OperationType.UPDATE: _apply_update,
    OperationType.DELETE: _apply_delete,
    OperationType.MOVE: _apply_move,
}


def apply_v4a_operations(operations: list[PatchOperation], file_ops: FileOperations) -> PatchResult:
    """两阶段：先整体校验，全部通过后才依次落盘。"""
    if not operations:
        return PatchResult(
            error="Patch contained no operations (missing `*** Begin Patch` header or no file sections).",
        )
    if errors := _validate_operations(operations, file_ops):
        return PatchResult(
            error="Patch validation failed (no files were modified):\n" + "\n".join(f"  • {e}" for e in errors),
        )
    files_modified: list[str] = []
    files_created: list[str] = []
    files_deleted: list[str] = []
    all_diffs: list[str] = []
    lint: dict[str, Any] = {}
    apply_errors: list[str] = []
    for op in operations:
        # 后续操作仍继续执行，最后统一汇报已改动与失败的文件。
        try:
            outcome = _APPLY[op.operation](op, file_ops)
        except Exception as e:
            apply_errors.append(f"Error processing {op.file_path}: {e}")
            continue
        if outcome.error is not None:
            apply_errors.append(f"Failed to {op.operation.value} {op.file_path}: {outcome.error}")
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
        if outcome.lint is not None:
            lint[op.file_path] = outcome.lint
        all_diffs.append(outcome.diff)
    return PatchResult(
        success=not apply_errors,
        diff="\n".join(all_diffs),
        files_modified=files_modified,
        files_created=files_created,
        files_deleted=files_deleted,
        lint=lint or None,
        error=(
            "Apply phase failed; files listed as modified/created/deleted were changed, the rest were not. "
            "Re-read the affected files before retrying:\n" + "\n".join(f"  • {e}" for e in apply_errors)
            if apply_errors
            else None
        ),
    )
