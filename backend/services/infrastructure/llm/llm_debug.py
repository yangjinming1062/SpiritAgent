import uuid
from typing import Any

from components import SETTINGS, get_logger

DEBUG_LOGGER_NAME = "llm.debug"
_debug_logger = get_logger(DEBUG_LOGGER_NAME)


def is_enabled() -> bool:
    # 主开关：尊重显式开关。根日志级别独立 —— 仅调 ``log_level = DEBUG`` 而未开此开关时不会把用户 prompt 发到 stdout。
    return bool(getattr(SETTINGS, "llm_debug_logging", False))


def _max_chars() -> int:
    return max(0, int(getattr(SETTINGS, "llm_debug_max_chars", 4000)))


def truncate_for_log(text: Any, *, max_chars: int | None = None) -> tuple[str | None, int]:
    """返回 ``(preview, original_len)``；非字符串输入被替换为简短类型标记，避免误导性的 repr。"""
    cap = _max_chars() if max_chars is None else max(0, max_chars)
    if text is None:
        return None, 0
    if not isinstance(text, str):
        return f"<{type(text).__name__}>", 0
    if cap and len(text) > cap:
        return text[:cap] + f"...[truncated, +{len(text) - cap} chars]", len(text)
    return text, len(text)


def _summarize_content_part(part: Any) -> Any:
    if isinstance(part, str):
        return truncate_for_log(part)[0]
    if not isinstance(part, dict):
        return f"<{type(part).__name__}>"
    ptype = part.get("type")
    if ptype in {"text", "input_text", "output_text"}:
        return {"type": ptype, "text": truncate_for_log(part.get("text", ""))[0]}
    if ptype in {"image_url", "input_image"}:
        # 省略 URL：图像内容可能带 base64 与供应商签名的过期 URL
        url = part.get("image_url", "")
        return {"type": ptype, "image_url": "<elided>" if url else None}
    if ptype in {"video_url", "input_video"}:
        url = part.get("video_url", "")
        return {"type": ptype, "video_url": "<elided>" if url else None}
    return {"type": ptype or "unknown", "keys": sorted(part.keys())}


def _summarize_response_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"type": getattr(item, "type", f"<{type(item).__name__}>")}
    out: dict[str, Any] = {"type": item.get("type") or item.get("role", "unknown")}
    if item.get("role"):
        out["role"] = item["role"]
    if item.get("name"):
        out["name"] = item["name"]
    if isinstance(item.get("content"), list):
        out["content_preview"] = [_summarize_content_part(part) for part in item["content"]]
    elif isinstance(item.get("content"), str):
        out["content_preview"] = truncate_for_log(item["content"])[0]
    if item.get("call_id"):
        out["call_id"] = item["call_id"]
    if item.get("arguments") is not None:
        out["arguments_preview"] = truncate_for_log(item["arguments"])[0]
    if item.get("output") is not None:
        out["output_preview"] = truncate_for_log(item["output"])[0]
    return out


def summarize_llm_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"model": kwargs.get("model"), "stream": bool(kwargs.get("stream"))}
    if (instructions := kwargs.get("instructions")) is not None:
        out["instructions_preview"] = truncate_for_log(instructions)[0]
    if (items := kwargs.get("input")) is not None:
        out["input"] = [_summarize_response_item(item) for item in items]
        out["num_input_items"] = len(items)
    if tools := kwargs.get("tools"):
        out["tools"] = [
            {"type": tool.get("type"), "name": tool.get("name")} for tool in tools if isinstance(tool, dict)
        ]
        out["num_tools"] = len(tools)
    for key in ("temperature", "top_p", "max_output_tokens", "reasoning", "text"):
        if kwargs.get(key) is not None:
            out[key] = kwargs[key]
    return out


def summarize_llm_response(response: Any) -> dict[str, Any]:
    if response is None:
        return {"present": False}
    out: dict[str, Any] = {
        "present": True,
        "id": getattr(response, "id", None),
        "status": getattr(response, "status", None),
    }
    output = getattr(response, "output", None) or []
    out["output"] = [_summarize_response_item(item) for item in output]
    out["num_output_items"] = len(output)
    if usage := getattr(response, "usage", None):
        out["usage"] = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    return out


def summarize_error(exc: BaseException) -> dict[str, Any]:
    """压缩错误到足够 grep 的粒度，但避免泄露供应商完整错误体（可能含 request ID / 内部 URL）。"""
    out: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    if classified := getattr(exc, "classified", None):
        out["reason"] = classified.reason.value if classified.reason else None
        out["status_code"] = classified.status_code
        out["retryable"] = classified.retryable
        out["should_fallback"] = classified.should_fallback
    if "status_code" not in out and (status := getattr(exc, "status_code", None)) is not None:
        out["status_code"] = status
    return out


def log_event(
    *,
    call_id: str,
    service: str,
    provider: str,
    model: str,
    call_site: str,
    phase: str,
    latency_ms: int | None = None,
    status: str | None = None,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    **extras: Any,
) -> None:
    """统一日志出口。``phase`` 用于还原时间线（request → response / error），``extras`` 直接透传层专属字段如 ``chain_index`` / ``next_provider``。"""
    if not is_enabled():
        return
    fields: dict[str, Any] = {
        "call_id": call_id,
        "service": service,
        "provider": provider,
        "model": model,
        "call_site": call_site,
        "phase": phase,
    }
    if latency_ms is not None:
        fields["latency_ms"] = latency_ms
    if status is not None:
        fields["status"] = status
    if request is not None:
        fields["request"] = request
    if response is not None:
        fields["response"] = response
    if error is not None:
        fields["error"] = error
    fields.update(extras)
    _debug_logger.debug("llm call", extra=fields)


def new_call_id() -> str:
    # 12 位 hex：熵足以跨并发调用唯一且保持 grep 友好（uuid4 全 32 位太长）。
    return uuid.uuid4().hex[:12]
