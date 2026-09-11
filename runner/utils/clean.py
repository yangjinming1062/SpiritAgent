import contextlib
import logging
import re

from .redact import redact_sensitive_text

logger = logging.getLogger(__name__)

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\][\s\S]*?(?:\x07|\x1b\\)"
    r"|[PX^_][\s\S]*?(?:\x1b\\)"
    r"|[\x20-\x2f]+[\x30-\x7e]"
    r"|[\x30-\x7e])"
    r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    r"|\x9d[\s\S]*?(?:\x07|\x9c)"
    r"|[\x80-\x9f]",
    re.DOTALL,
)
_HAS_ESCAPE = re.compile(r"[\x1b\x80-\x9f]")

_FENCE_OPEN_RE = re.compile(r"^\s*```[\w]*\s*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n\s*```\s*$")


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text) if text and _HAS_ESCAPE.search(text) else text


def strip_fence(text: str) -> str:
    """去除包裹终端输出的成对 Markdown 代码围栏；中间孤立的 ``` 不会被动。"""
    if not text:
        return text
    open_match = _FENCE_OPEN_RE.match(text)
    if not open_match:
        return text
    close_match = _FENCE_CLOSE_RE.search(text)
    if not close_match:
        return text
    inner = text[open_match.end() : close_match.start()]
    return inner


def clean_output(text: str) -> str:
    """对工具输出按固定顺序清洗：先去 ANSI 跳脱码，再去代码围栏，最后脱敏。"""
    with contextlib.suppress(Exception):
        text = strip_ansi(text)
    with contextlib.suppress(Exception):
        text = strip_fence(text)
    # redact_sensitive_text 是安全关键，失败即整段屏蔽，保证原始凭据绝不进入 LLM。
    if text:
        try:
            text = redact_sensitive_text(text)
        except Exception:
            logger.warning("redact_sensitive_text failed; masking entire output as safety fallback", exc_info=True)
            text = "***REDACTED (error)***"
    return text
