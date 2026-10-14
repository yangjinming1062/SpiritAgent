import difflib
import os
from typing import Any

from spirit_agent_runner.contracts.output import get_max_line_length, get_max_lines
from spirit_agent_runner.features.files.binary_extensions import has_binary_extension
from spirit_agent_runner.features.files.models import (
    _CONTEXT_DELIM_RE,
    _SEARCH_LINE_RE,
    _UTF8_BOM,
    DEFAULT_READ_LIMIT,
    DEFAULT_READ_OFFSET,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SEARCH_OFFSET,
    MAX_EDIT_BYTES,
    MAX_MATCH_CONTENT,
    ReadResult,
    SearchMatch,
    SearchResult,
)


def _not_utf8_error(path: str) -> str:
    return f"{path} is not valid UTF-8 text; it cannot be edited safely with this tool."


def too_large_to_edit_error(path: str, size: int) -> str:
    return (
        f"{path} is {size:,} bytes; files over {MAX_EDIT_BYTES // (1024 * 1024)} MB cannot be edited with this tool. "
        "Use the terminal tool instead."
    )


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
    """返回 (LF 文本, 原换行符)；匹配在 LF 上做，写回时恢复。"""
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
