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


def fuzzy_find_and_replace(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int, str | None, str | None]:
    """按逐渐放宽的策略链查找并替换文本。

    返回 (new_content, match_count, strategy_name, error)。成功时前三项有意义，失败时仅最后一项。
    """
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
        ("context_aware", _strategy_context_aware),
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

            # 转义漂移防护：当匹配策略非 exact 时，是靠归一化才匹配的。
            # 若 new_string 含有 shell/JSON 风格的转义（\' 或 \"），而文件
            # 匹配区域实际没有这些字符，几乎一定是工具调用序列化漂移——
            # 模型输入了撇号/引号，传输层多加了一个反斜杠。原样写入会污染文件。
            if strategy_name != "exact":
                drift_err = _detect_escape_drift(content, matches, old_string, new_string)
                if drift_err:
                    return content, 0, None, drift_err

            # 执行替换。当匹配策略非 exact 时，文件实际缩进可能与 LLM 给的
            # old_string/new_string 不同（如 LLM 用 2 空格而文件是 4 空格）。
            # 需按缩进差平移 new_string，让结果贴合文件真实缩进。
            #
            # LLM 经常把 JSON 工具调用参数里的 tab/CR 序列化成两个字符 ``\t`` 和
            # ``\r``（反斜杠+字母），而非真实的控制字节。若原样写入，文件里
            # 就会出现字面反斜杠序列，破坏真实 tab 缩进。
            #
            # 策略：仅当匹配区域实际包含对应真实控制字符时才反转义。这与
            # ``_detect_escape_drift`` 的区域启发式一致，能保留合法的字面
            # ``"\t"`` 写入（如修补含 tab 字面量的 Python 源码）。
            #
            # ``\n`` 故意排除：JSON 中换行能正确序列化，反转义反而会破坏源
            # 代码字符串常量里的转义序列。
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
    """检测 new_string 中由工具调用序列化引入的转义漂移。

    若 ``\'`` 或 ``\"`` 同时出现在 old_string 和 new_string（模型复制时带入的
    上下文），但匹配区域实际没有这些字符，说明传输层在撇号/引号旁多加了
    反斜杠——原样写入会把 ``\'`` 字面量写进源码。
    """
    # 廉价前置检查：new_string 中没有可疑转义时直接跳过，常规正确路径无开销。
    if "\\'" not in new_string and '\\"' not in new_string:
        return None

    # 汇总匹配区域——新内容将替换这里。若该区域已含可疑转义，说明模型是真
    # 想保留它们（某些语言/转义字符串本就如此），按合法写入接受。
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
    """将 ``new_string`` 的缩进调整到与 ``file_region`` 一致。

    非精确模糊匹配后调用：LLM 给的缩进可能与文件不同（如 2 空格 vs 4 空格），
    模糊匹配仍能命中，但原样写入会破坏文件缩进。

    算法：对 new_string 每个非空行，用相对偏移（line_indent - llm_base）
    重新锚定到文件基础缩进。空行与比 llm 基础缩进更浅的行直接对齐到文件基础。
    """
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

    # 逐行重新缩放：把 LLM 的基础缩进前缀替换为文件的基础前缀，保留 LLM
    # 额外加上的相对嵌套。这与 Roo Code (multi-search-replace.ts:466-500)
    # 思路一致：在贴合文件实际缩进风格的同时保留 LLM 想要的相对层级。
    out_lines: list[str] = []
    for line in new_string.split("\n"):
        if not line.strip():
            # Blank lines: leave whitespace untouched.
            out_lines.append(line)
            continue
        line_indent = _leading_whitespace(line)
        if line_indent.startswith(old_indent):
            # 常规情形：行首包含 LLM 基础缩进（可能再多一些），把前缀换成文件的。
            remainder = line[len(old_indent) :]
            out_lines.append(file_indent + remainder)
        else:
            # 缩进浅于 LLM 基础（如 new_string 起始处的去缩进行），对齐文件基础。
            out_lines.append(file_indent + line.lstrip(" \t"))
    return "\n".join(out_lines)


def _maybe_unescape_new_string(new_string: str, content: str, matches: list[tuple[int, int]]) -> str:
    """有条件地反转义 new_string 中的 ``\\t``/``\\r``。

    LLM 经常在 JSON 工具调用参数里把 tab/CR 写成两个字符 ``\t``/``\r``，原样
    写入会把字面反斜杠+字母对污染到 tab 缩进的文件中。

    仅当匹配区域本身包含对应的真实控制字符时才反转义，避免误改合法字面
    ``"\t"``（如 ``sep = "\t"`` 形式的 Python 源码）。``\n`` 故意排除：
    JSON 能正确序列化换行，反转义反而会破坏字符串字面量。
    """
    # 廉价前置检查，常规正确路径无开销。
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

    def normalize(s):
        return re.sub(r"[ \t]+", " ", s)

    pattern_normalized = normalize(pattern)
    content_normalized = normalize(content)

    matches_in_normalized = _strategy_exact(content_normalized, pattern_normalized)

    if not matches_in_normalized:
        return []

    return _map_normalized_positions(content, content_normalized, matches_in_normalized)


def _strategy_indentation_flexible(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 4：完全忽略行首缩进后再匹配。"""
    content_lines = content.split("\n")
    content_stripped_lines = [line.lstrip() for line in content_lines]
    pattern_lines = [line.lstrip() for line in pattern.split("\n")]

    return _find_normalized_matches(content, content_lines, content_stripped_lines, pattern, "\n".join(pattern_lines))


def _strategy_escape_normalized(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 5：将转义序列（``\\n``/``\\t``/``\\r``）还原为真实控制字符后再匹配。"""

    def unescape(s):
        return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")

    pattern_unescaped = unescape(pattern)

    if pattern_unescaped == pattern:
        # 无可还原的转义，跳过
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
    """建立 原字符索引 → 归一化后字符索引 的映射表。

    UNICODE_MAP 替换可能扩展字符（em-dash → '--'、省略号 → '...'），导致
    归一化字符串比原串更长，因此需要该映射把归一化坐标转回原坐标。
    """
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

        # 向前走到 orig_to_norm[orig_end] >= norm_end 为止
        orig_end = orig_start
        while orig_end < orig_len and orig_to_norm[orig_end] < norm_end:
            orig_end += 1

        results.append((orig_start, orig_end))

    return results


def _strategy_unicode_normalized(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 7：Unicode 归一化（智能引号/长短破折号/不间断空格 → ASCII），再跑 exact + line_trimmed。

    UNICODE_MAP 部分替换会扩长字符（如 em-dash → '--'），所以坐标需通过
    ``_build_orig_to_norm_map`` 反向映射回原字符串，不能直接拷贝。
    """
    # 双侧归一化。任一侧含 Unicode 变体都需归一化；两侧都未变化才跳过。
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
    # 比较前归一化，但保留原 content 用于计算字符偏移。
    norm_pattern = _unicode_normalize(pattern)
    norm_content = _unicode_normalize(content)

    pattern_lines = norm_pattern.split("\n")
    if len(pattern_lines) < 2:
        return []

    first_line = pattern_lines[0].strip()
    last_line = pattern_lines[-1].strip()

    norm_content_lines = norm_content.split("\n")
    # 但偏移计算必须用原始行，避免归一化造成的索引漂移
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

    # 阈值策略：单一候选 0.50，多候选 0.70——避免宽松中段相似度误命中无关块。
    threshold = 0.50 if candidate_count == 1 else 0.70

    for i in potential_matches:
        if pattern_line_count <= 2:
            similarity = 1.0
        else:
            content_middle = "\n".join(norm_content_lines[i + 1 : i + pattern_line_count - 1])
            pattern_middle = "\n".join(pattern_lines[1:-1])
            similarity = SequenceMatcher(None, content_middle, pattern_middle).ratio()

        if similarity >= threshold:
            # 用原始行计算偏移，确保文件中字符位置正确
            start_pos, end_pos = _calculate_line_positions(orig_content_lines, i, i + pattern_line_count, len(content))
            matches.append((start_pos, end_pos))

    return matches


def _strategy_context_aware(content: str, pattern: str) -> list[tuple[int, int]]:
    """策略 9：按行相似度匹配，至少 50% 的行需高相似。"""
    pattern_lines = pattern.split("\n")
    content_lines = content.split("\n")

    if not pattern_lines:
        return []

    matches = []
    pattern_line_count = len(pattern_lines)

    for i in range(len(content_lines) - pattern_line_count + 1):
        block_lines = content_lines[i : i + pattern_line_count]

        high_similarity_count = 0
        for p_line, c_line in zip(pattern_lines, block_lines, strict=True):
            sim = SequenceMatcher(None, p_line.strip(), c_line.strip()).ratio()
            if sim >= 0.80:
                high_similarity_count += 1

        # 至少 50% 的行高相似才视为命中
        if high_similarity_count >= len(pattern_lines) * 0.5:
            start_pos, end_pos = _calculate_line_positions(content_lines, i, i + pattern_line_count, len(content))
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


def _map_normalized_positions(
    original: str,
    normalized: str,
    normalized_matches: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """将归一化坐标尽力映射回原坐标；适用于空白归一化场景。"""
    if not normalized_matches:
        return []

    orig_to_norm = []  # orig_to_norm[i] = position in normalized

    orig_idx = 0
    norm_idx = 0

    while orig_idx < len(original) and norm_idx < len(normalized):
        if original[orig_idx] == normalized[norm_idx]:
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            norm_idx += 1
        elif original[orig_idx] in " \t" and normalized[norm_idx] == " ":
            # 原始为空格/制表符，归一化为单空格
            orig_to_norm.append(norm_idx)
            orig_idx += 1
            # 先不前进 norm_idx，等所有空白消化完
            if orig_idx < len(original) and original[orig_idx] not in " \t":
                norm_idx += 1
        elif original[orig_idx] in " \t":
            orig_to_norm.append(norm_idx)
            orig_idx += 1
        else:
            # 理论上不会发生，归一化不会引入非空白差异
            orig_to_norm.append(norm_idx)
            orig_idx += 1

    while orig_idx < len(original):
        orig_to_norm.append(len(normalized))
        orig_idx += 1

    # 反向映射：每个归一化位置 → 对应的原字符区间
    norm_to_orig_start = {}
    norm_to_orig_end = {}

    for orig_pos, norm_pos in enumerate(orig_to_norm):
        if norm_pos not in norm_to_orig_start:
            norm_to_orig_start[norm_pos] = orig_pos
        norm_to_orig_end[norm_pos] = orig_pos

    original_matches = []
    for norm_start, norm_end in normalized_matches:
        orig_start = (
            norm_to_orig_start[norm_start]
            if norm_start in norm_to_orig_start
            else min(i for i, n in enumerate(orig_to_norm) if n >= norm_start)
        )

        orig_end = (
            norm_to_orig_end[norm_end - 1] + 1
            if norm_end - 1 in norm_to_orig_end
            else orig_start + (norm_end - norm_start)
        )

        while orig_end < len(original) and original[orig_end] in " \t":
            orig_end += 1

        original_matches.append((orig_start, min(orig_end, len(original))))

    return original_matches


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
    """仅在「找不到 old_string」场景下返回「是不是想找……」提示。

    模糊匹配、多匹配、转义漂移等 ``match_count == 0`` 的错误原因各不相同，
    强加「did you mean」反而误导，故仅对真正未命中场景触发。无内容时返回空串。
    """
    if match_count != 0:
        return ""
    if not error or not error.startswith("Could not find"):
        return ""
    hint = find_closest_lines(old_string, content)
    if not hint:
        return ""
    return "\n\nDid you mean one of these sections?\n" + hint
