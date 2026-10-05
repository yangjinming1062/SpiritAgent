import json
from typing import Any

from components import get_logger, is_time_context_text

logger = get_logger(__name__)

# Responses API 输入媒体 part 类型 → 占位文本；较早的历史条目与格式恢复请求不携带媒体。
_MEDIA_PART_PLACEHOLDERS = {"input_image": "[screenshot]", "input_video": "[video]"}
# 确定性窗口：保留的最近输入项数、其中仍携带媒体的最近项数、历史条目字符上限。
_MAX_RECENT_ITEMS = 40
_MEDIA_RECENT_ITEMS = 10
_MAX_CHARS_PER_ITEM = 15000


def _repair_json_text(raw: str) -> str | None:
    """字符串感知的 JSON 修复：转义字符串内的控制字符；字符串外删除紧邻闭括号的尾逗号和多余的闭括号，并按嵌套顺序补齐缺失的闭括号。字符串未结束或括号错配时返回 None。"""
    out: list[str] = []
    closers: list[str] = []
    # 字符串外、后面尚未出现其他内容的逗号；紧随其后的是闭括号（或输入结束）时才删除。
    comma_index: int | None = None
    in_string = False
    chars = iter(raw)
    for ch in chars:
        if in_string:
            if ch == "\\":
                out.append(ch + next(chars, ""))
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        elif ch in " \t\r\n":
            out.append(ch)
        elif ch in "}]":
            if comma_index is not None:
                out[comma_index] = ""
                comma_index = None
            if not closers:
                continue
            if closers.pop() != ch:
                return None
            out.append(ch)
        else:
            comma_index = len(out) if ch == "," else None
            if ch == '"':
                in_string = True
            elif ch in "{[":
                closers.append("}" if ch == "{" else "]")
            out.append(ch)
    if in_string:
        return None
    if comma_index is not None:
        out[comma_index] = ""
    return "".join(out) + "".join(reversed(closers))


def parse_tool_call_arguments(raw_args: str, tool_name: str) -> dict[str, Any] | None:
    """尽力解析并修复 LLM tool-call 参数；空值、None 与 null 是无参调用，返回空字典。不可修复或不是 JSON 对象时返回 None，调用方须向模型报告参数错误而不是以空参数派发。参数含用户数据与凭据，日志只记录长度。"""
    if not raw_args:
        return {}
    raw = raw_args.strip()
    parsed = _repair_json(raw, tool_name)
    if isinstance(parsed, dict):
        return parsed
    logger.warning(
        "Invalid tool_call arguments, tool not dispatched",
        extra={"tool_name": tool_name, "raw_len": len(raw)},
    )
    return None


def _repair_json(raw: str, tool_name: str) -> object:
    """返回解析结果；空值、None 与 null 是无参调用，返回 {}；不可修复时返回 None。"""
    if not raw:
        logger.warning("Sanitized empty tool_call arguments", extra={"tool_name": tool_name})
        return {}

    if raw in ("None", "null"):
        logger.warning("Sanitized null tool_call arguments", extra={"tool_name": tool_name})
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

    repaired = _repair_json_text(raw)
    if repaired is None:
        return None
    try:
        parsed = json.loads(repaired)
    except ValueError:
        return None
    logger.warning("Repaired malformed tool_call arguments", extra={"tool_name": tool_name, "raw_len": len(raw)})
    return parsed


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


def replace_media_parts(parts: list) -> list:
    return [
        {"type": "input_text", "text": _MEDIA_PART_PLACEHOLDERS[part["type"]]}
        if isinstance(part, dict) and part.get("type") in _MEDIA_PART_PLACEHOLDERS
        else part
        for part in parts
    ]


def _normalize_older_response_item(item: dict, *, replace_images: bool, max_chars: int) -> dict:
    normalized = dict(item)
    if replace_images:
        # 消息的媒体在 content，多模态工具结果的媒体在 function_call_output 的 output。
        for key in ("content", "output"):
            if isinstance(normalized.get(key), list):
                normalized[key] = replace_media_parts(normalized[key])
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
    current_max_chars: int = 0,
    current_message_id: int | None = None,
) -> dict[str, Any]:
    """deterministic Responses input-window fallback; instructions are never dropped。``current_max_chars`` 是本轮用户输入的字符上限（不低于历史条目上限），由调用方按上下文窗口给出。"""
    items = context["input"]
    keep_start = max(0, len(items) - _MAX_RECENT_ITEMS)
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
    kept_indices: list[int | None] = list(range(keep_start, len(items)))
    checkpoint_indices = set(context.get("checkpoint_indices", ()))
    current_start = _trailing_user_start(items)
    # 手动重试时工具结果排在原请求之后，按持久化来源保留原请求的长度预算和附件。
    current_indices = {
        index
        for index, source_id in enumerate(context.get("source_message_ids", []))
        if current_message_id is not None and source_id == current_message_id
    }
    current_chars = max(current_max_chars, _MAX_CHARS_PER_ITEM)
    kept = [
        _normalize_older_response_item(
            item,
            replace_images=index < len(tail) - _MEDIA_RECENT_ITEMS and keep_start + index not in current_indices,
            max_chars=current_chars
            if keep_start + index >= current_start or keep_start + index in current_indices
            else _MAX_CHARS_PER_ITEM,
        )
        for index, item in enumerate(tail)
    ]
    if keep_start > 0:
        anchor_index = next(
            (
                index
                for index in range(keep_start - 1, -1, -1)
                if index not in checkpoint_indices and _is_user_anchor(items[index])
            ),
            None,
        )
        retained_indices = sorted(index for index in checkpoint_indices if index < keep_start)
        if anchor_index is not None:
            retained_indices.append(anchor_index)
            retained_indices.sort()
        removed = keep_start - len(retained_indices)
        marker = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"[... {removed} early conversation items removed for context window management ...]",
                },
            ],
        }
        prefix = [
            _normalize_older_response_item(
                items[index],
                replace_images=index not in current_indices,
                max_chars=current_chars if index in current_indices else _MAX_CHARS_PER_ITEM,
            )
            for index in retained_indices
        ]
        prefix.append(marker)
        kept = prefix + kept
        kept_indices = [*retained_indices, None, *kept_indices]
    user_indices = set(context.get("user_input_indices", ()))
    return {
        "instructions": context["instructions"],
        "input": kept,
        "user_input_indices": [index for index, original in enumerate(kept_indices) if original in user_indices],
    }
