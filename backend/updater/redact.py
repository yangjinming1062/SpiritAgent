"""更新器日志与错误输出脱敏；私钥、令牌与凭据不落盘。"""

import re

_KEY_PATTERNS = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?:Bearer\s+|Token\s+|token[=:]\s*)[A-Za-z0-9._~+/=-]{8,}",
    r"(?:password|passwd|secret|api[_-]?key|auth[_-]?token)\s*[=:]\s*\S+",
)
_COMPILED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _KEY_PATTERNS)


def redact_text(text: str) -> str:
    for pattern in _COMPILED:
        text = pattern.sub("[REDACTED]", text)
    return text


def sanitize_line(line: str, *, limit: int = 2000) -> str:
    """单行输出截断与脱敏；控制字符压缩为可见转义。"""

    line = line.replace("\x00", "\\0").replace("\r", "\\r")
    if len(line) > limit:
        line = line[:limit] + f"…[已截断 {len(line) - limit} 字符]"
    return redact_text(line)
