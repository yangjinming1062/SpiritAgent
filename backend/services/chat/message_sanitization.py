import json
import re
from typing import Any

from components import get_logger

logger = get_logger(__name__)


# Responses API 输入媒体 part 类型 → 老轮次占位文本；``truncate_responses_context`` 窗口外替换使用。
_MEDIA_PART_PLACEHOLDERS = {"input_image": "[screenshot]", "input_video": "[video]"}


def _escape_invalid_chars_in_json_strings(raw: str) -> str:
    out: list[str] = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(raw[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
        i += 1
    return "".join(out)


def _repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> str:
    """尽力修复 LLM tool-call 参数中的畸形 JSON；不可修复时返回 ``"{}"``，避免单个坏调用阻塞聊天循环。"""
    raw_stripped = raw_args.strip() if isinstance(raw_args, str) else ""

    if not raw_stripped:
        logger.warning("Sanitized empty tool_call arguments", extra={"tool_name": tool_name})
        return "{}"

    if raw_stripped == "None":
        logger.warning("Sanitized Python-None tool_call arguments", extra={"tool_name": tool_name})
        return "{}"

    try:
        parsed = json.loads(raw_stripped, strict=False)
        reserialised = json.dumps(parsed, separators=(",", ":"))
        if reserialised != raw_stripped:
            logger.warning("Repaired unescaped control chars in tool_call arguments", extra={"tool_name": tool_name})
        return reserialised
    except (TypeError, ValueError):
        pass

    fixed = re.sub(r",\s*([}\]])", r"\1", raw_stripped)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket
    # 终止条件：仅当末尾 }/] 多于开括号时继续剪枝，最多执行 len(fixed) 次。
    while True:
        try:
            json.loads(fixed)
            break
        except json.JSONDecodeError:
            trailing_curly = fixed.endswith("}") and fixed.count("}") > fixed.count("{")
            trailing_bracket = fixed.endswith("]") and fixed.count("]") > fixed.count("[")
            if trailing_curly or trailing_bracket:
                fixed = fixed[:-1]
            else:
                break

    try:
        json.loads(fixed)
        logger.warning(
            "Repaired malformed tool_call arguments",
            extra={"tool_name": tool_name, "raw": raw_stripped[:80], "fixed": fixed[:80]},
        )
        return fixed
    except json.JSONDecodeError:
        pass

    try:
        escaped = _escape_invalid_chars_in_json_strings(fixed)
        if escaped != fixed:
            json.loads(escaped)
            logger.warning(
                "Repaired control-char-laced tool_call arguments",
                extra={"tool_name": tool_name, "raw": raw_stripped[:80], "escaped": escaped[:80]},
            )
            return escaped
    except (TypeError, ValueError):
        pass

    logger.warning(
        "Unrepairable tool_call arguments, replaced with empty object",
        extra={"tool_name": tool_name, "raw": raw_stripped[:80]},
    )
    return "{}"


def _truncate_response_text(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return (
            value
            if len(value) <= max_chars
            else value[:max_chars] + f"\n\n[... Truncated from {len(value)} chars to save context ...]"
        )
    if isinstance(value, list):
        return [_truncate_response_text(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {
            key: item if key in ("image_url", "video_url") else _truncate_response_text(item, max_chars)
            for key, item in value.items()
        }
    return value


def _normalize_older_response_item(item: dict, *, replace_images: bool, max_chars: int) -> dict:
    normalized = dict(item)
    if replace_images and isinstance(normalized.get("content"), list):
        normalized["content"] = [
            {"type": "input_text", "text": _MEDIA_PART_PLACEHOLDERS[part["type"]]}
            if isinstance(part, dict) and part.get("type") in _MEDIA_PART_PLACEHOLDERS
            else part
            for part in normalized["content"]
        ]
    return _truncate_response_text(normalized, max_chars)


def truncate_responses_context(
    context: dict[str, Any],
    max_recent_items: int = 40,
    normalize_older_than: int = 10,
    max_chars_per_item: int = 15000,
) -> dict[str, Any]:
    """deterministic Responses input-window fallback; instructions are never dropped."""
    items = context["input"]
    keep_start = max(0, len(items) - max_recent_items)
    for _ in range(max_recent_items):
        if keep_start <= 0 or items[keep_start].get("type") != "function_call_output":
            break
        keep_start -= 1

    tail = items[keep_start:]
    kept = [
        _normalize_older_response_item(
            item,
            replace_images=index < len(tail) - normalize_older_than,
            max_chars=max_chars_per_item,
        )
        for index, item in enumerate(tail)
    ]
    if keep_start > 0:
        anchor = next((item for item in items[:keep_start] if item.get("role") == "user"), None)
        removed = keep_start - (1 if anchor is not None else 0)
        marker = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"[... {removed} early conversation items removed for context window management ...]",
                },
            ],
        }
        kept = (
            [_normalize_older_response_item(anchor, replace_images=True, max_chars=max_chars_per_item), marker]
            if anchor is not None
            else [marker]
        )
    return {"instructions": context["instructions"], "input": kept}
