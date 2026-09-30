import json
import re
from typing import Any

from components import get_logger, is_time_context_text

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


def parse_tool_call_arguments(raw_args: str, tool_name: str) -> dict[str, Any]:
    """尽力解析 LLM tool-call 参数；空值、非对象或不可修复时返回空参数，避免单个坏调用阻塞聊天循环。"""
    if not raw_args:
        return {}
    parsed = _repair_json(raw_args.strip(), tool_name)
    return parsed if isinstance(parsed, dict) else {}


def _repair_json(raw: str, tool_name: str) -> object:
    if not raw:
        logger.warning("Sanitized empty tool_call arguments", extra={"tool_name": tool_name})
        return {}

    if raw == "None":
        logger.warning("Sanitized Python-None tool_call arguments", extra={"tool_name": tool_name})
        return {}

    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        parsed = json.loads(raw, strict=False)
    except ValueError:
        pass
    else:
        logger.warning("Repaired unescaped control chars in tool_call arguments", extra={"tool_name": tool_name})
        return parsed

    fixed = re.sub(r",\s*([}\]])", r"\1", raw)
    open_curly = fixed.count("{") - fixed.count("}")
    open_bracket = fixed.count("[") - fixed.count("]")
    if open_curly > 0:
        fixed += "}" * open_curly
    if open_bracket > 0:
        fixed += "]" * open_bracket
    # 终止条件：仅当末尾 }/] 多于开括号时继续剪枝，最多执行 len(fixed) 次。
    while True:
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError:
            trailing_curly = fixed.endswith("}") and fixed.count("}") > fixed.count("{")
            trailing_bracket = fixed.endswith("]") and fixed.count("]") > fixed.count("[")
            if not (trailing_curly or trailing_bracket):
                break
            fixed = fixed[:-1]
        else:
            logger.warning(
                "Repaired malformed tool_call arguments",
                extra={"tool_name": tool_name, "raw": raw[:80], "fixed": fixed[:80]},
            )
            return parsed

    escaped = _escape_invalid_chars_in_json_strings(fixed)
    if escaped != fixed:
        try:
            parsed = json.loads(escaped)
        except ValueError:
            pass
        else:
            logger.warning(
                "Repaired control-char-laced tool_call arguments",
                extra={"tool_name": tool_name, "raw": raw[:80], "escaped": escaped[:80]},
            )
            return parsed

    logger.warning(
        "Unrepairable tool_call arguments, replaced with empty object",
        extra={"tool_name": tool_name, "raw": raw[:80]},
    )
    return {}


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


def _is_user_anchor(item: dict[str, Any]) -> bool:
    if item.get("role") != "user":
        return False
    content = item.get("content")
    if isinstance(content, str):
        return not is_time_context_text(content)
    if isinstance(content, list) and len(content) == 1:
        part = content[0]
        if isinstance(part, dict) and part.get("type") == "input_text":
            return not is_time_context_text(part.get("text") or "")
    return True


def _trailing_user_start(items: list[dict[str, Any]]) -> int:
    """末尾连续用户消息（本轮用户输入与运行时资料）的起点；它们是本轮任务，不适用历史条目的字符上限。"""
    start = len(items)
    while start > 0 and items[start - 1].get("role") == "user":
        start -= 1
    return start


def truncate_responses_context(
    context: dict[str, Any],
    max_recent_items: int = 40,
    normalize_older_than: int = 10,
    max_chars_per_item: int = 15000,
    current_max_chars: int = 0,
    current_message_id: int | None = None,
) -> dict[str, Any]:
    """deterministic Responses input-window fallback; instructions are never dropped。``current_max_chars`` 是本轮用户输入的字符上限（不低于历史条目上限），由调用方按上下文窗口给出。"""
    items = context["input"]
    keep_start = max(0, len(items) - max_recent_items)
    call_positions = {
        item["call_id"]: index
        for index, item in enumerate(items)
        if item.get("type") == "function_call" and item.get("call_id")
    }
    # 并行调用先成批写入，再写结果；按 call_id 扩展边界，不能只退到最近一个调用。
    for index in range(len(items) - 1, -1, -1):
        if index < keep_start:
            break
        item = items[index]
        if item.get("type") == "function_call_output":
            keep_start = min(keep_start, call_positions.get(item.get("call_id"), index))

    tail = items[keep_start:]
    current_start = _trailing_user_start(items)
    # 手动重试时工具结果排在原请求之后，按持久化来源保留原请求的长度预算和附件。
    current_indices = {
        index
        for index, source_id in enumerate(context.get("source_message_ids", []))
        if current_message_id is not None and source_id == current_message_id
    }
    current_chars = max(current_max_chars, max_chars_per_item)
    kept = [
        _normalize_older_response_item(
            item,
            replace_images=index < len(tail) - normalize_older_than and keep_start + index not in current_indices,
            max_chars=current_chars
            if keep_start + index >= current_start or keep_start + index in current_indices
            else max_chars_per_item,
        )
        for index, item in enumerate(tail)
    ]
    if keep_start > 0:
        anchor_index = next((index for index in range(keep_start - 1, -1, -1) if _is_user_anchor(items[index])), None)
        anchor = items[anchor_index] if anchor_index is not None else None
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
        prefix = (
            [
                _normalize_older_response_item(
                    anchor,
                    replace_images=anchor_index not in current_indices,
                    max_chars=current_chars if anchor_index in current_indices else max_chars_per_item,
                ),
                marker,
            ]
            if anchor is not None
            else [marker]
        )
        kept = prefix + kept
    return {"instructions": context["instructions"], "input": kept}
