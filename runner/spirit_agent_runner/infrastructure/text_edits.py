import re
from collections.abc import Callable
from difflib import SequenceMatcher

UNICODE_MAP = {
    "\u201c": '"',
    "\u201d": '"',  # smart double quotes
    "\u2018": "'",
    "\u2019": "'",  # smart single quotes
    "\u2014": "--",
    "\u2013": "-",  # em/en dashes
    "\u2026": "...",
    "\u00a0": " ",  # ellipsis and non-breaking space
}


def _unicode_normalize(text: str) -> str:
    """将智能引号/破折号/省略号等 Unicode 字符归一化为 ASCII 等价形式。"""
    for char, repl in UNICODE_MAP.items():
        text = text.replace(char, repl)
    return text


def _unescape_controls(text: str) -> str:
    """把字面 ``\\n``/``\\t``/``\\r`` 还原为真实控制字符。"""
    return text.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")


def fuzzy_find_and_replace(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int, str | None, str | None]:
    """按策略链查找并替换；返回 (new_content, match_count, strategy_name, error)。"""
    if not old_string:
        return content, 0, None, "old_string cannot be empty"

    if old_string == new_string:
        return content, 0, None, "old_string and new_string are identical"

    strategies: list[tuple[str, Callable]] = [
        ("exact", _strategy_exact),
        ("line_trimmed", _strategy_line_trimmed),
        ("whitespace_normalized", _strategy_whitespace_normalized),
        ("indentation_flexible", _strategy_indentation_flexible),
        ("escape_normalized", _strategy_escape_normalized),
        ("trimmed_boundary", _strategy_trimmed_boundary),
        ("unicode_normalized", _strategy_unicode_normalized),
        ("block_anchor", _strategy_block_anchor),
    ]

    for strategy_name, strategy_fn in strategies:
        matches = strategy_fn(content, old_string)

        if matches:
            if len(matches) > 1 and not replace_all:
                return (
                    content,
                    0,
                    None,
                    (
                        f"Found {len(matches)} matches for old_string. Provide more context to make it unique, or use replace_all=True."
                    ),
                )

            # 转义漂移防护：非 exact 命中时若 new 含 \'/\" 而匹配区没有，按序列化漂移拒绝写入。
            if strategy_name != "exact":
                drift_err = _detect_escape_drift(content, matches, old_string, new_string)
                if drift_err:
                    return content, 0, None, drift_err

            # escape_normalized 命中时 new 也须一并反转义，否则字面 \n 会写进文件。
            if strategy_name == "escape_normalized":
                old_string, new_string = _unescape_controls(old_string), _unescape_controls(new_string)
            # 非 exact 命中时按 old_string 把 new_string 缩进对齐文件。
            effective_new = _maybe_unescape_new_string(new_string, content, matches)
            new_content = _apply_replacements(
                content,
                matches,
                effective_new,
                old_string=old_string if strategy_name != "exact" else None,
            )
            return new_content, len(matches), strategy_name, None

    return content, 0, None, "Could not find a match for old_string in the file"


def _detect_escape_drift(content: str, matches: list[tuple[int, int]], old_string: str, new_string: str) -> str | None:
    """检测工具调用序列化引入的转义漂移（``\'``/``\"`` 出现在两侧但匹配区域没有）。"""
    if "\\'" not in new_string and '\\"' not in new_string:
        return None

    # 匹配区已含可疑转义视为有意保留，放行。
    matched_regions = "".join(content[start:end] for start, end in matches)

    for suspect in ("\\'", '\\"'):
        if suspect in new_string and suspect in old_string and suspect not in matched_regions:
            plain = suspect[1]  # "'" or '"'
            return (
                f"Escape-drift detected: old_string and new_string contain "
                f"the literal sequence {suspect!r} but the matched region of "
                f"the file does not. This is almost always a tool-call "
                f"serialization artifact where an apostrophe or quote got "
                f"prefixed with a spurious backslash. Re-read the file with "
                f"read_file and pass old_string/new_string without "
                f"backslash-escaping {plain!r} characters."
            )
    return None


def _leading_whitespace(line: str) -> str:
    """返回行首的空白前缀（空格/制表符）。"""
    i = 0
    while i < len(line) and line[i] in (" ", "\t"):
        i += 1
    return line[:i]


def _first_meaningful_line(text: str) -> str | None:
    """返回 ``text`` 中第一行非空白内容；若全为空则返回 None。"""
    for line in text.split("\n"):
        if line.strip():
            return line
    return None


def _reindent_replacement(file_region: str, old_string: str, new_string: str) -> str:
    """把 ``new_string`` 的缩进重锚到 ``file_region``，保留相对嵌套（见 README 模糊匹配缩进）。"""
    if not new_string:
        return new_string

    old_first = _first_meaningful_line(old_string)
    file_first = _first_meaningful_line(file_region)
    if old_first is None or file_first is None:
        return new_string

    old_indent = _leading_whitespace(old_first)
    file_indent = _leading_whitespace(file_first)

    if old_indent == file_indent:
        return new_string

    # 把 LLM 基础缩进前缀换成文件前缀，保留相对嵌套。
    out_lines: list[str] = []
    for line in new_string.split("\n"):
        if not line.strip():
            out_lines.append(line)
            continue
        line_indent = _leading_whitespace(line)
        if line_indent.startswith(old_indent):
            remainder = line[len(old_indent) :]
            out_lines.append(file_indent + remainder)
        else:
            out_lines.append(file_indent + line.lstrip(" \t"))
    return "\n".join(out_lines)


def _maybe_unescape_new_string(new_string: str, content: str, matches: list[tuple[int, int]]) -> str:
    """匹配区域含真实 tab/CR 时才反转义 ``\\t``/``\\r``；``\\n`` 故意不反转义。"""
    if "\\t" not in new_string and "\\r" not in new_string:
        return new_string

    matched_regions = "".join(content[start:end] for start, end in matches)
    out = new_string
    if "\\t" in out and "\t" in matched_regions:
        out = out.replace("\\t", "\t")
    if "\\r" in out and "\r" in matched_regions:
        out = out.replace("\\r", "\r")
    return out


def _apply_replacements(
    content: str,
    matches: list[tuple[int, int]],
    new_string: str,
    old_string: str | None = None,
) -> str:
    """在给定位置应用替换；非精确匹配时按 old_string 重新缩进 new_string。"""
    sorted_matches = sorted(matches, key=lambda x: x[0], reverse=True)

    result = content
    for start, end in sorted_matches:
        if old_string is not None:
            file_region = content[start:end]
            adjusted = _reindent_replacement(file_region, old_string, new_string)
        else:
            adjusted = new_string
        result = result[:start] + adjusted + result[end:]

    return result


def _strategy_exact(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 1：精确字符串匹配（不重叠，行为同 str.replace）。"""
    matches = []
    start = 0
    while (pos := content.find(pattern, start)) != -1:
        matches.append((pos, pos + len(pattern)))
        start = pos + len(pattern)
    return matches


def _strategy_line_trimmed(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 2：逐行 trim 首尾空白后再匹配。"""
    pattern_lines = [line.strip() for line in pattern.split("\n")]
    pattern_normalized = "\n".join(pattern_lines)

    content_lines = content.split("\n")
    content_normalized_lines = [line.strip() for line in content_lines]

    return _find_normalized_matches(content, content_lines, content_normalized_lines, pattern, pattern_normalized)


def _strategy_whitespace_normalized(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 3：将多个连续空格/制表符压缩为单空格，保留换行。"""

    def normalize(s: str) -> str:
        return re.sub(r"[ \t]+", " ", s)

    pattern_normalized = normalize(pattern)
    content_normalized = normalize(content)

    matches_in_normalized = _strategy_exact(content_normalized, pattern_normalized)

    if not matches_in_normalized:
        return []

    return _map_whitespace_positions(content, matches_in_normalized)


def _strategy_indentation_flexible(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 4：完全忽略行首缩进后再匹配。"""
    content_lines = content.split("\n")
    content_stripped_lines = [line.lstrip() for line in content_lines]
    pattern_lines = [line.lstrip() for line in pattern.split("\n")]

    return _find_normalized_matches(content, content_lines, content_stripped_lines, pattern, "\n".join(pattern_lines))


def _strategy_escape_normalized(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 5：将转义序列（``\\n``/``\\t``/``\\r``）还原为真实控制字符后再匹配。"""
    pattern_unescaped = _unescape_controls(pattern)

    if pattern_unescaped == pattern:
        return []

    return _strategy_exact(content, pattern_unescaped)


def _strategy_trimmed_boundary(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 6：仅 trim 首行与末行的空白，处理边界空白差异。"""
    pattern_lines = pattern.split("\n")
    if not pattern_lines:
        return []

    pattern_lines[0] = pattern_lines[0].strip()
    if len(pattern_lines) > 1:
        pattern_lines[-1] = pattern_lines[-1].strip()

    modified_pattern = "\n".join(pattern_lines)

    content_lines = content.split("\n")

    matches = []
    pattern_line_count = len(pattern_lines)

    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i : i + pattern_line_count]

        check_lines = block_lines.copy()
        check_lines[0] = check_lines[0].strip()
        if len(check_lines) > 1:
            check_lines[-1] = check_lines[-1].strip()

        if "\n".join(check_lines) == modified_pattern:
            start_pos, end_pos = _calculate_line_positions(content_lines, i, i + pattern_line_count, len(content))
            matches.append((start_pos, end_pos))

    return matches


def _build_orig_to_norm_map(original: str) -> list[int]:
    """原字符索引 → 归一化后索引；UNICODE_MAP 替换会扩长字符，坐标须反向映射。"""
    result: list[int] = []
    norm_pos = 0
    for char in original:
        result.append(norm_pos)
        repl = UNICODE_MAP.get(char)
        norm_pos += len(repl) if repl is not None else 1
    result.append(norm_pos)  # 哨兵：原串末字符之后的位置
    return result


def _map_positions_norm_to_orig(orig_to_norm: list[int], norm_matches: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """将归一化字符串中的 (start, end) 坐标转换回原字符串坐标。"""
    norm_to_orig_start: dict[int, int] = {}
    for orig_pos, norm_pos in enumerate(orig_to_norm[:-1]):
        if norm_pos not in norm_to_orig_start:
            norm_to_orig_start[norm_pos] = orig_pos

    results: list[tuple[int, int]] = []
    orig_len = len(orig_to_norm) - 1  # 原字符数量

    for norm_start, norm_end in norm_matches:
        if norm_start not in norm_to_orig_start:
            continue
        orig_start = norm_to_orig_start[norm_start]

        orig_end = orig_start
        while orig_end < orig_len and orig_to_norm[orig_end] < norm_end:
            orig_end += 1

        results.append((orig_start, orig_end))

    return results


def _strategy_unicode_normalized(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 7：Unicode 归一化后再 exact + line_trimmed；坐标经 ``_build_orig_to_norm_map`` 反查。"""
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)
    if norm_content == content and norm_pattern == pattern:
        return []

    norm_matches = _strategy_exact(norm_content, norm_pattern)
    if not norm_matches:
        norm_matches = _strategy_line_trimmed(norm_content, norm_pattern)

    if not norm_matches:
        return []

    orig_to_norm = _build_orig_to_norm_map(content)
    return _map_positions_norm_to_orig(orig_to_norm, norm_matches)


def _strategy_block_anchor(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 8：以首末行锚定 + Unicode 归一化的块匹配，阈值宽松。"""
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)

    pattern_lines = norm_pattern.split("\n")
    if len(pattern_lines) < 2:
        return []

    first_line = pattern_lines[0].strip()
    last_line = pattern_lines[-1].strip()

    norm_content_lines = norm_content.split("\n")
    orig_content_lines = content.split("\n")

    pattern_line_count = len(pattern_lines)

    potential_matches = []
    for i in range(len(norm_content_lines) - pattern_line_count + 1):
        if (
            norm_content_lines[i].strip() == first_line
            and norm_content_lines[i + pattern_line_count - 1].strip() == last_line
        ):
            potential_matches.append(i)

    matches = []
    candidate_count = len(potential_matches)

    # 单候选阈值 0.50，多候选 0.70，避免误命中无关块。
    threshold = 0.50 if candidate_count == 1 else 0.70

    for i in potential_matches:
        if pattern_line_count <= 2:
            similarity = 1.0
        else:
            content_middle = "\n".join(norm_content_lines[i + 1 : i + pattern_line_count - 1])
            pattern_middle = "\n".join(pattern_lines[1:-1])
            similarity = SequenceMatcher(None, content_middle, pattern_middle).ratio()

        if similarity >= threshold:
            start_pos, end_pos = _calculate_line_positions(orig_content_lines, i, i + pattern_line_count, len(content))
            matches.append((start_pos, end_pos))

    return matches


def _calculate_line_positions(
    content_lines: list[str],
    start_line: int,
    end_line: int,
    content_length: int,
) -> tuple[int, int]:
    """根据行号区间计算原字符串的字符起止位置。"""
    start_pos = sum(len(line) + 1 for line in content_lines[:start_line])
    end_pos = sum(len(line) + 1 for line in content_lines[:end_line]) - 1
    end_pos = min(content_length, end_pos)
    return start_pos, end_pos


def _find_normalized_matches(
    content: str,
    content_lines: list[str],
    content_normalized_lines: list[str],
    pattern: str,
    pattern_normalized: str,
) -> list[tuple[int, int]]:
    """在归一化内容中查找匹配，再映射回原内容坐标。"""
    pattern_norm_lines = pattern_normalized.split("\n")
    num_pattern_lines = len(pattern_norm_lines)

    matches = []

    for i in range(len(content_normalized_lines) - num_pattern_lines + 1):
        block = "\n".join(content_normalized_lines[i : i + num_pattern_lines])

        if block == pattern_normalized:
            start_pos, end_pos = _calculate_line_positions(content_lines, i, i + num_pattern_lines, len(content))
            matches.append((start_pos, end_pos))

    return matches


def _map_whitespace_positions(original: str, normalized_matches: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """把空白压缩后的坐标映射回原串；结尾空白按整段覆盖。"""
    run_start: list[int] = []  # 归一化位置 → 对应原文区间起点
    run_end: list[int] = []  # 归一化位置 → 对应原文区间终点（不含）
    i = 0
    while i < len(original):
        j = i + 1
        if original[i] in " \t":
            while j < len(original) and original[j] in " \t":
                j += 1
        run_start.append(i)
        run_end.append(j)
        i = j
    return [(run_start[start], run_end[end - 1]) for start, end in normalized_matches]


def find_closest_lines(old_string: str, content: str, context_lines: int = 2, max_results: int = 3) -> str:
    """查找 content 中与 old_string 最相似的若干行，用于「是不是想找……」提示。"""
    if not old_string or not content:
        return ""

    old_lines = old_string.splitlines()
    content_lines = content.splitlines()

    if not old_lines or not content_lines:
        return ""

    anchor = old_lines[0].strip()
    if not anchor:
        candidates = [line.strip() for line in old_lines if line.strip()]
        if not candidates:
            return ""
        anchor = candidates[0]

    scored = []
    for i, line in enumerate(content_lines):
        stripped = line.strip()
        if not stripped:
            continue
        ratio = SequenceMatcher(None, anchor, stripped).ratio()
        if ratio > 0.3:
            scored.append((ratio, i))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    top = scored[:max_results]

    parts = []
    seen_ranges = set()
    for _, line_idx in top:
        start = max(0, line_idx - context_lines)
        end = min(len(content_lines), line_idx + len(old_lines) + context_lines)
        key = (start, end)
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        snippet = "\n".join(f"{start + j + 1:4d}| {content_lines[start + j]}" for j in range(end - start))
        parts.append(snippet)

    if not parts:
        return ""

    return "\n---\n".join(parts)


def format_no_match_hint(error: str | None, match_count: int, old_string: str, content: str) -> str:
    """仅对真正未命中返回「是不是想找……」；多匹配/漂移等 0 命中不误导。"""
    if match_count != 0:
        return ""
    if not error or not error.startswith("Could not find"):
        return ""
    hint = find_closest_lines(old_string, content)
    if not hint:
        return ""
    return "\n\nDid you mean one of these sections?\n" + hint
