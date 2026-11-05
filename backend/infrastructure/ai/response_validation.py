"""Responses 终态校验；流式与完整响应共享供应商失败、拒绝和未完成的语义。"""

from collections.abc import Iterable

from openai.types.responses import Response, ResponseOutputItem

from infrastructure.ai.error_classifier import (
    ClassifiedError,
    FailoverReason,
    LLMRuntimeError,
    classify_api_error,
    is_content_policy_response,
)
from infrastructure.ai.providers.base import ProviderError


class IncompleteLlmResponseError(RuntimeError):
    """供应商响应未完成；仅保留终止原因、状态和错误码。"""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"LLM response not completed: {detail}")


def _failure_detail(response: Response) -> str:
    parts = [f"status={response.status}"]
    if response.incomplete_details and (reason := response.incomplete_details.reason):
        parts.append(f"reason={reason[:80]}")
    if response.error and (code := response.error.code):
        parts.append(f"error_code={str(code)[:80]}")
    return ", ".join(parts)


def validate_response_output(output: Iterable[ResponseOutputItem]) -> None:
    if any(part.type == "refusal" for item in output if item.type == "message" for part in item.content):
        raise LLMRuntimeError(ClassifiedError(FailoverReason.content_policy_blocked, None, "LLM response refused"))


def validate_response(response: Response) -> None:
    """先处理明确错误与拒绝，再确认完成状态；正文和工具的业务校验由调用方负责。"""
    if response.error is not None:
        classified = classify_api_error(
            ProviderError("LLM response failed", body={"error": response.error.model_dump()})
        )
        raise LLMRuntimeError(ClassifiedError(classified.reason, classified.status_code, _failure_detail(response)))
    details = response.incomplete_details
    if is_content_policy_response(response.model_extra or {}) or (details and details.reason == "content_filter"):
        raise LLMRuntimeError(ClassifiedError(FailoverReason.content_policy_blocked, None, "LLM response refused"))
    validate_response_output(response.output or ())
    if response.status != "completed":
        raise IncompleteLlmResponseError(
            details.reason if details and details.reason else "unknown", _failure_detail(response)
        )
