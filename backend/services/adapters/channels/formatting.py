import re

# 行内代码 / 围栏代码块：去壳保内容（IM 是纯文本场，代码内容原样保留）。
_FENCE_RE = re.compile(r"^\s*(```|~~~)[^\n]*$", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# 加粗/斜体标记（保留内部文本；拆两层覆盖 ***粗斜体***）。星号允许词内成对；下划线按 CommonMark 只在词边界成对——词内成对会把 snake_case 误剥成 snakecase。
_BOLD_ASTERISK_RE = re.compile(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
# Markdown 链接 [text](url) → text（url）；裸图片 ![alt](url) → （图片 url）。
_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\s]+)\)")
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")
# 标题井号 / 无序列表标记 / 引用角标：去标记保文本。
_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_LIST_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
# 简易表格：分隔线行整行丢弃，行首竖线去掉。
_TABLE_SEP_RE = re.compile(r"^\s*\|?[-\s:|]+\|?\s*$", re.MULTILINE)
_TABLE_BAR_RE = re.compile(r"^\s*\|", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """把 LLM 输出的 Markdown 降级为 IM 可读的纯文本：去标记保留语义内容，不做完整 Markdown 解析。"""
    if not text:
        return ""
    out = _FENCE_RE.sub("", text)
    out = _IMAGE_RE.sub(r"（图片 \2）", out)
    out = _LINK_RE.sub(r"\1（\2）", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    for _ in range(2):
        out = _BOLD_ASTERISK_RE.sub(r"\2", out)
        out = _BOLD_UNDERSCORE_RE.sub(r"\2", out)
    out = _STRIKE_RE.sub(r"\1", out)
    out = _HEADER_RE.sub("", out)
    out = _LIST_RE.sub("", out)
    out = _QUOTE_RE.sub("", out)
    out = _TABLE_SEP_RE.sub("", out)
    out = _TABLE_BAR_RE.sub("", out)
    # 去壳后残留的连续空行折叠为一行，段落结构仍在。
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_CHUNK_SEPARATORS = ("\n\n", "\n", " ")


def _split_level(text: str, seps: tuple[str, ...], limit: int) -> list[str]:
    """按本层分隔符切开并贪心合并，合并处还原本层分隔符；超限片段交给下一层，各层都切不开才硬切。"""
    if len(text) <= limit:
        return [text]
    if not seps:
        return [text[i : i + limit] for i in range(0, len(text), limit)]
    sep, rest = seps[0], seps[1:]
    chunks: list[str] = []
    current: str | None = None
    for part in text.split(sep):
        first, *others = _split_level(part, rest, limit)
        if current is not None and len(current) + len(sep) + len(first) <= limit:
            current = f"{current}{sep}{first}"
        else:
            if current is not None:
                chunks.append(current)
            current = first
        # 下一层切出的相邻片段已无法合并，各自成片；末片仍可与本层后续片段合并。
        if others:
            chunks.append(current)
            chunks.extend(others[:-1])
            current = others[-1]
    if current is not None:
        chunks.append(current)
    return chunks


def chunk_text(text: str, limit: int) -> list[str]:
    """按 段落 → 行 → 空格 边界把长回复切为 ≤limit 的分片，片内保留原分隔符，切点处的分隔符丢弃。"""
    if limit <= 0 or len(text) <= limit:
        return [text]
    return [chunk for chunk in _split_level(text, _CHUNK_SEPARATORS, limit) if chunk.strip()]
