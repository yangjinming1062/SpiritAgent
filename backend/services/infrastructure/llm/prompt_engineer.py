"""单次文本与视觉模型调用、终态检查和错误诊断。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from components import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_RETRY_MIN_TIMEOUT,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    strip_outer_code_fence,
)
from openai.types.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .error_classifier import ClassifiedError, FailoverReason, LlmCallBlockedError, LLMRuntimeError, classify_api_error
from .llm_client import resolve_provider_chain, resolve_vision_chain
from .llm_fallback import execute_with_fallback
from .llm_retry import call_with_retry
from .providers import (
    ChatProvider,
    ProviderConfig,
    ServiceType,
    resolve_context_tokens,
)
from .responses import approx_responses_tokens, build_responses_kwargs
from .user_config import UserLlmConfig

logger = get_logger(__name__)


class VisualReasoningError(RuntimeError):
    """视觉推理失败；异常文本可展示，供应商诊断只写入日志。"""


class IncompleteLlmResponseError(RuntimeError):
    """供应商响应未完成；异常只含状态、终止原因与错误码。"""


class LlmAttemptDiagnostic(BaseModel):
    """可持久化的调用诊断，不包含输入、模型正文或异常原文。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    repair_count: int
    fallback_count: int
    max_output_tokens: int | None
    reasoning_effort: str | None = None
    response_status: str | None = None
    termination_reason: str | None = None
    output_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    validation: Literal[
        "ok",
        "invalid_json",
        "invalid_contract",
        "empty_output",
        "incomplete",
        "refused",
        "unexpected_output",
        "call_error",
    ] = "call_error"
    validation_error: str | None = None
    fields: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    error_type: str | None = None


class LlmJsonValidationError(ValueError):
    """应用校验器提供固定错误码和契约字段，不携带模型输出。"""

    def __init__(self, code: str, *, fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.fields = fields
        super().__init__(code)


async def chat(
    user_id: int | None,
    system_prompt: str,
    user_payload: str,
    *,
    provider_config: ProviderConfig | None = None,
) -> str:
    """单次非流式 chat 往返；未指定供应商时在独立短会话中解析配置，模型调用期间不占数据库连接。空内容视为错误，避免把空 prompt 透传给生图供应商。"""
    if provider_config is None:
        async with SESSION_LOCAL() as config_db:
            chain = await resolve_provider_chain(config_db, user_id, ServiceType.llm)
    else:
        chain = [provider_config]
    config = UserLlmConfig(chain[0] if chain else None, tuple(chain[1:]), user_id)
    text = (await call_llm_once(config, system_prompt, user_payload, max_output_tokens=LLM_MAX_OUTPUT_TOKENS)).strip()
    return text


def _response_failure_detail(response: Response) -> str:
    details = getattr(response, "incomplete_details", None)
    reason = details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)
    error = getattr(response, "error", None)
    code = error.get("code") if isinstance(error, dict) else getattr(error, "code", None)
    parts = [f"status={response.status}"]
    if isinstance(reason, str) and reason.strip():
        parts.append(f"reason={reason[:80]}")
    if isinstance(code, str | int) and str(code).strip():
        parts.append(f"error_code={str(code)[:80]}")
    return ", ".join(parts)


async def _call_text_response(
    provider: ChatProvider,
    request: dict[str, Any],
    *,
    context_length: int | None = None,
) -> str:
    retry_kwargs = {"context_length": context_length} if context_length is not None else {}
    try:
        async with asyncio.timeout(max(float(SETTINGS.llm_request_timeout_seconds), LLM_RETRY_MIN_TIMEOUT)):
            response = await call_with_retry(provider.raw_client(), **retry_kwargs, **request)
    except TimeoutError as exc:
        raise LLMRuntimeError(ClassifiedError(FailoverReason.timeout, None, "LLM request timed out")) from exc
    return _response_text(response)


def _response_text(response: Response) -> str:
    details = response.incomplete_details
    if (details is not None and details.reason == "content_filter") or any(
        part.type == "refusal" for item in response.output if item.type == "message" for part in item.content
    ):
        raise LLMRuntimeError(ClassifiedError(FailoverReason.content_policy_blocked, None, "LLM response refused"))
    if response.status != "completed":
        raise IncompleteLlmResponseError(f"LLM response not completed: {_response_failure_detail(response)}")
    text = response.output_text
    if not text.strip():
        raise LLMRuntimeError(ClassifiedError(FailoverReason.empty_result, None, "LLM response has no output text"))
    return text


def _output_token_budget(
    context_length: int,
    instructions: str,
    input_items: list[dict[str, Any]],
    max_output_tokens: int | None,
) -> int | None:
    if max_output_tokens is None:
        return None
    available = (
        context_length - approx_responses_tokens(instructions, input_items) - min(1024, max(1, context_length // 20))
    )
    # 超长输入保留原样，由供应商返回窗口错误。
    return min(max_output_tokens, available) if available > 0 else max_output_tokens


async def call_llm_once(
    llm_cfg: UserLlmConfig,
    system_prompt: str,
    user_payload: Any,
    *,
    max_output_tokens: int | None,
    reasoning_effort: str | None = None,
    json_output: bool = False,
    temperature: float | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """沿能力链执行单次非流式调用；温度按归一化刻度换算，推理档位按当前供应商支持集映射。"""
    user_content = (
        json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    )
    input_items = [{"role": "user", "content": [{"type": "input_text", "text": user_content}]}]

    async def call(provider: ChatProvider) -> str:
        context_length = resolve_context_tokens(provider.config)
        request = build_responses_kwargs(
            model=provider.config.model,
            instructions=system_prompt,
            input_items=input_items,
            max_output_tokens=_output_token_budget(context_length, system_prompt, input_items, max_output_tokens),
            temperature=provider.scale_temperature(temperature) if temperature is not None else None,
            reasoning=await provider.reasoning_param(reasoning_effort),
        )
        if json_output:
            request.update(
                await provider.structured_response_options({"type": "object"}, name="json_object", allow_tools=False),
            )
        if before_call is not None:
            await before_call()
        return await _call_text_response(
            provider,
            request,
            context_length=context_length,
        )

    return await execute_with_fallback(llm_cfg.chain, ChatProvider, call, user_id=llm_cfg.user_id)


def _schema_fields(schema: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            fields.update(value)
        if isinstance(value, dict):
            fields.update(_schema_fields(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    fields.update(_schema_fields(item))
    return fields


def _json_validation_feedback(exc: ValueError, known_fields: set[str]) -> tuple[str, list[str]]:
    if isinstance(exc, LlmJsonValidationError):
        return exc.code, sorted(known_fields.intersection(exc.fields))
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        fields = {part for error in errors for part in error["loc"] if isinstance(part, str) and part in known_fields}
        if any(error["type"] == "extra_forbidden" for error in errors):
            fields.add("unexpected_fields")
        return "schema_validation", sorted(fields)
    return "contract_validation", []


async def call_llm_json[T](
    llm_cfg: UserLlmConfig,
    system_prompt: str,
    user_payload: Any,
    *,
    schema: dict[str, Any],
    schema_name: str,
    parse_output: Callable[[Any], T],
    repair_prompt: str,
    max_output_tokens: int | None,
    reasoning_effort: str | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> T:
    """校验成功后才完成供应商调用；整条链共用一次格式修正预算。"""
    attempts = diagnostics if diagnostics is not None else []
    known_fields = _schema_fields(schema)
    repair_used = False
    provider_index = -1

    def record(diagnostic: LlmAttemptDiagnostic) -> None:
        attempts.append(diagnostic)
        if diagnostic.validation != "ok":
            logger.warning(
                "structured LLM output failed",
                extra={
                    "user_id": llm_cfg.user_id,
                    "schema_name": schema_name,
                    **diagnostic.model_dump(),
                },
            )

    async def call(provider: ChatProvider) -> T:
        nonlocal repair_used, provider_index
        provider_index += 1
        repair = False
        feedback: dict[str, Any] | None = None
        while True:
            payload = user_payload
            if feedback is not None:
                payload = {
                    **(user_payload if isinstance(user_payload, dict) else {"data": user_payload}),
                    "validation_feedback": feedback,
                }
            instructions = system_prompt + (repair_prompt if repair else "")
            input_items = [
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]},
            ]
            context_length = resolve_context_tokens(provider.config)
            budget = _output_token_budget(context_length, instructions, input_items, max_output_tokens)
            options = await provider.structured_response_options(
                schema,
                name=schema_name,
                allow_tools=False,
                repair=repair,
            )
            request = build_responses_kwargs(
                model=provider.config.model,
                instructions=instructions,
                input_items=input_items,
                max_output_tokens=budget,
                reasoning=await provider.reasoning_param(reasoning_effort),
                temperature=provider.scale_temperature(0.0) if repair else None,
            )
            request.update(options)
            if before_call is not None:
                await before_call()
            diagnostic = LlmAttemptDiagnostic(
                provider=provider.config.provider_name,
                model=provider.config.model,
                repair_count=int(repair_used),
                fallback_count=provider_index,
                max_output_tokens=budget,
                reasoning_effort=(request.get("reasoning") or {}).get("effort"),
            )
            try:
                # 不限输出 token 时仍沿用配置的调用时限，整个请求与 SDK 网络重试共用该时限。
                async with asyncio.timeout(max(float(SETTINGS.llm_request_timeout_seconds), LLM_RETRY_MIN_TIMEOUT)):
                    response = await call_with_retry(
                        provider.structured_response_client(repair=repair, before_submit=before_call),
                        context_length=context_length,
                        **request,
                    )
            except LlmCallBlockedError:
                raise
            except Exception as exc:
                classified = (
                    ClassifiedError(FailoverReason.timeout, None, "LLM request timed out")
                    if isinstance(exc, TimeoutError)
                    else classify_api_error(exc)
                )
                diagnostic.failure_reason = classified.reason.value
                diagnostic.error_type = type(exc.__cause__ or exc).__name__
                record(diagnostic)
                if isinstance(exc, TimeoutError):
                    raise LLMRuntimeError(classified) from exc
                raise
            diagnostic.response_status = response.status
            diagnostic.termination_reason = response.incomplete_details.reason if response.incomplete_details else None
            diagnostic.output_chars = len(response.output_text)
            if response.usage is not None:
                diagnostic.input_tokens = response.usage.input_tokens
                diagnostic.output_tokens = response.usage.output_tokens
                diagnostic.reasoning_tokens = getattr(response.usage.output_tokens_details, "reasoning_tokens", None)
            try:
                raw = _response_text(response)
            except IncompleteLlmResponseError:
                diagnostic.validation = "incomplete"
                record(diagnostic)
                raise
            except LLMRuntimeError as exc:
                diagnostic.validation = (
                    "refused" if exc.classified.reason == FailoverReason.content_policy_blocked else "empty_output"
                )
                record(diagnostic)
                raise
            if any(item.type == "function_call" for item in response.output):
                diagnostic.validation = "unexpected_output"
                record(diagnostic)
                raise LLMRuntimeError(
                    ClassifiedError(FailoverReason.format_error, None, "Structured response returned a tool call"),
                )
            try:
                try:
                    parsed = json.loads(strip_outer_code_fence(raw))
                except json.JSONDecodeError:
                    parsed = parse_llm_json(raw)
                    if parsed is None:
                        raise LlmJsonValidationError("invalid_json") from None
                result = parse_output(parsed)
            except ValueError as exc:
                code, fields = _json_validation_feedback(exc, known_fields)
                diagnostic.validation = "invalid_json" if code == "invalid_json" else "invalid_contract"
                diagnostic.validation_error = code
                diagnostic.fields = fields
                record(diagnostic)
                if not repair_used:
                    repair_used = True
                    repair = True
                    feedback = {"error": code, "fields": fields}
                    continue
                raise LLMRuntimeError(
                    ClassifiedError(
                        FailoverReason.format_error,
                        None,
                        f"{schema_name}: invalid structured output ({code})",
                    ),
                ) from None
            diagnostic.validation = "ok"
            record(diagnostic)
            return result

    return await execute_with_fallback(llm_cfg.chain, ChatProvider, call, user_id=llm_cfg.user_id)


async def vision_chat(
    user_id: int | None,
    system_prompt: str,
    user_payload: str,
    *,
    reference_images: tuple[str, ...],
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """带实际图像的视觉推理；独立短会话解析配置，无可用模型时明确失败。"""
    if not reference_images or any(not uri for uri in reference_images):
        raise ValueError("visual reasoning requires readable reference images")
    async with SESSION_LOCAL() as chain_db:
        chain = await resolve_vision_chain(chain_db, user_id)
    if not chain:
        raise VisualReasoningError("视觉分析服务未配置，请联系管理员")
    content = [{"type": "input_image", "image_url": uri} for uri in reference_images]
    content.append({"type": "input_text", "text": user_payload})
    input_items = [{"role": "user", "content": content}]

    async def _describe(provider: ChatProvider) -> str:
        if before_submit is not None:
            await before_submit()
        context_length = resolve_context_tokens(provider.config)
        response = await call_with_retry(
            provider.raw_client(),
            context_length=context_length,
            **build_responses_kwargs(
                model=provider.config.model,
                instructions=system_prompt,
                input_items=input_items,
                max_output_tokens=_output_token_budget(
                    context_length,
                    system_prompt,
                    input_items,
                    LLM_MAX_OUTPUT_TOKENS,
                ),
            ),
        )
        result = strip_outer_code_fence(response.output_text) if response.status == "completed" else ""
        if not result:
            # 未完成或空结果可换下一家视觉模型
            raise LLMRuntimeError(
                ClassifiedError(FailoverReason.empty_result, None, f"vision response unusable ({response.status})"),
            )
        return result

    try:
        return await execute_with_fallback(chain, ChatProvider, _describe, user_id=user_id)
    except LlmCallBlockedError:
        raise
    except Exception as exc:
        logger.warning("visual reasoning failed", extra={"user_id": user_id}, exc_info=True)
        raise VisualReasoningError("视觉分析失败，请稍后重试") from exc
