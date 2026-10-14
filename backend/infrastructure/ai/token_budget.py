from typing import Any

from infrastructure.ai.error_classifier import ClassifiedError, FailoverReason, LLMRuntimeError
from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.ai.providers.registry import resolve_context_tokens
from infrastructure.ai.responses import approx_responses_tokens


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
    headroom = min(1024, max(1, window // 20))
    available = window - estimated - headroom
    if available <= 0:
        raise LLMRuntimeError(
            ClassifiedError(FailoverReason.context_overflow, None, "输入无法容纳于当前模型卡片的上下文窗口"),
        )
    return min(limit, available)
