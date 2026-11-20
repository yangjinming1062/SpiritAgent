import json
from collections.abc import Sequence
from typing import Any

from infrastructure.ai.error_classifier import ClassifiedError, FailoverReason, LLMRuntimeError
from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.ai.providers.registry import resolve_context_tokens
from infrastructure.ai.responses import approx_responses_tokens
from infrastructure.text import approx_text_tokens


class ContextOverflowError(LLMRuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(ClassifiedError(FailoverReason.context_overflow, None, message))


def _window_headroom(window: int) -> int:
    return min(1024, max(1, window // 20))


def material_input_window(config: ProviderConfig, window: int | None = None) -> int:
    """扣除模型输出和封装余量，可供必需输入和参考资料共同使用的空间。"""
    window = resolve_context_tokens(config) if window is None else window
    return window - min(config.max_output_tokens or 0, window // 3) - _window_headroom(window)


def request_output_budget(config: ProviderConfig, request: dict[str, Any]) -> int:
    window = resolve_context_tokens(config)
    limit = config.max_output_tokens
    if limit is None or limit <= 0:
        raise ValueError("LLM 模型卡片缺少输出 Token 上限")
    requested = request.get("max_output_tokens")
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
            raise ValueError("输出 Token 限额须为正整数")
        limit = min(limit, requested)
    estimated = approx_responses_tokens(str(request.get("instructions") or ""), request.get("input"))
    estimated += approx_responses_tokens("", request.get("tools", []))
    estimated += approx_responses_tokens("", request.get("text", {}))
    estimated += approx_responses_tokens("", request.get("extra_body", {}))
    available = window - estimated - _window_headroom(window)
    if available <= 0:
        raise ContextOverflowError("输入无法容纳于当前模型卡片的上下文窗口")
    return min(limit, available)


def json_tokens(value: Any) -> int:
    return approx_text_tokens(json.dumps(value, ensure_ascii=False))


def material_budget(
    providers: Sequence[ProviderConfig], instructions: str, payload: Any, schema: dict[str, Any]
) -> int:
    """共享资料取能容纳必需输入的模型的共同余量；按供应商分批时只传当前模型。"""
    required = approx_text_tokens(instructions) + json_tokens(payload) + json_tokens(schema) + 32
    rooms = (material_input_window(provider) - required for provider in providers)
    return min((room for room in rooms if room > 0), default=0)


def fit_text_prefix(text: str, budget: int) -> str:
    """计算 JSON 字符串实际转义后的大小，返回可完整放入的前缀。"""
    if budget <= 0:
        return ""
    if json_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if json_tokens(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low]
