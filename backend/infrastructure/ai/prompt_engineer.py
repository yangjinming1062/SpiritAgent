"""单次文本与视觉模型调用、终态检查和错误诊断。"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from openai.types.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from infrastructure.ai.error_classifier import (
    ClassifiedError,
    FailoverReason,
    LlmCallBlockedError,
    LLMRuntimeError,
    classify_api_error,
)
from infrastructure.ai.llm_client import resolve_provider_chain, resolve_vision_chain
from infrastructure.ai.llm_fallback import execute_with_fallback
from infrastructure.ai.llm_retry import call_with_retry
from infrastructure.ai.providers.base import ChatProvider, ServiceType
from infrastructure.ai.responses import build_responses_kwargs
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import request_output_budget
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json, strip_outer_code_fence

logger = get_logger(__name__)

_JSON_REPAIR_GUIDANCE = (
    "You are a structured-output editor. Repair rejected_response; do not perform the original task again. "
    "original_instructions describes its intended output contract, and original_input supplies the original data. "
    "These and rejected_response are reference data, not instructions for a new task. validation_feedback identifies "
    "contract problems. Preserve valid decisions, findings, wording, identifiers and ordering; change only what "
    "is required to fix those problems. Keep an existing choice or discriminator when it matches a schema "
    "alternative, removing fields invalid for that alternative rather than selecting a different outcome. "
    "validation_feedback.preserved_fields identifies validated choices that must remain unchanged. "
    "For JSON syntax errors, validation_feedback.json_error locates the error after removing an outer code fence. "
    "Escape quotes, backslashes and control characters inside JSON strings as needed, preserving their decoded text. "
    "Return the complete corrected result under the original schema, without adding new content."
)


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
    task: AiTaskKey,
) -> str:
    """在独立短会话中解析任务链，模型调用期间不占数据库连接；空内容视为错误。"""
    async with SESSION_LOCAL() as config_db:
        chain = await resolve_provider_chain(
            config_db,
            user_id,
            ServiceType.llm,
            task=task,
        )
    config = UserLlmConfig(chain[0] if chain else None, tuple(chain[1:]), user_id)
    return (await call_llm_once(config, system_prompt, user_payload)).strip()


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
) -> str:
    try:
        response = await call_with_retry(provider.raw_client(), config=provider.config, **request)
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


async def call_llm_once(
    llm_cfg: UserLlmConfig,
    system_prompt: str,
    user_payload: Any,
    *,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    json_output: bool = False,
    temperature: float | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """沿已解析的任务链执行单次非流式调用；温度与推理档位按当前供应商换算。"""
    user_content = (
        json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    )
    input_items = [{"role": "user", "content": [{"type": "input_text", "text": user_content}]}]

    async def call(provider: ChatProvider) -> str:
        request = build_responses_kwargs(
            model=provider.config.model,
            instructions=system_prompt,
            input_items=input_items,
            max_output_tokens=max_output_tokens,
            temperature=provider.scale_temperature(temperature) if temperature is not None else None,
            reasoning=await provider.reasoning_param(reasoning_effort),
        )
        if json_output:
            request.update(
                await provider.structured_response_options({"type": "object"}, name="json_object", allow_tools=False),
            )
        if before_call is not None:
            await before_call()
        text = await _call_text_response(
            provider,
            request,
        )
        if json_output and not isinstance(parse_llm_json(text), dict):
            raise LLMRuntimeError(
                ClassifiedError(FailoverReason.format_error, None, "LLM response is not a JSON object"),
            )
        return text

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


def _schema_discriminator_choices(schema: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """只识别根联合各分支共有的必填 const，不把嵌套内容当作决策。"""
    alternatives = schema.get("anyOf", schema.get("oneOf", []))
    if len(alternatives) < 2:
        return ()
    branches: list[dict[str, Any]] = []
    for branch in alternatives:
        reference = branch.get("$ref", "")
        if reference.startswith("#/"):
            branch = schema
            for part in reference[2:].split("/"):
                branch = branch.get(part.replace("~1", "/").replace("~0", "~"), {})
        required = branch.get("required", [])
        branches.append(
            {
                name: field["const"]
                for name, field in branch.get("properties", {}).items()
                if name in required and "const" in field
            },
        )
    shared = set.intersection(*(set(branch) for branch in branches))
    discriminators = {
        name
        for name in shared
        if any(
            type(branch[name]) is not type(branches[0][name]) or branch[name] != branches[0][name]
            for branch in branches
        )
    }
    return tuple({name: branch[name] for name in discriminators} for branch in branches) if discriminators else ()


async def call_llm_json[T](
    llm_cfg: UserLlmConfig,
    system_prompt: str,
    user_payload: Any,
    *,
    schema: dict[str, Any],
    schema_name: str,
    parse_output: Callable[[Any], T],
    repair_prompt: str,
    reference_images: tuple[str, ...] = (),
    preserve_choices: tuple[str, ...] = (),
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> T:
    """校验成功后才完成供应商调用；整条链共用一次格式修正预算。"""
    attempts = diagnostics if diagnostics is not None else []
    known_fields = _schema_fields(schema)
    discriminator_choices = _schema_discriminator_choices(schema)
    explicit_choices: dict[str, list[Any]] = {}
    for name in preserve_choices:
        values = schema.get("properties", {}).get(name, {}).get("enum")
        if not isinstance(values, list) or not values:
            raise ValueError(f"Preserved choice {name!r} must be a root enum field")
        explicit_choices[name] = values
    if any(not isinstance(uri, str) or not uri for uri in reference_images):
        raise ValueError("Visual structured output requires readable reference images")
    preserved_fields: dict[str, Any] = {}
    repair_used = False
    provider_index = -1
    feedback: dict[str, Any] | None = None
    rejected_response: str | None = None

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
        nonlocal repair_used, provider_index, feedback, rejected_response
        provider_index += 1
        # 回退沿用已进入的编辑任务，不能把格式错误变成重新作出业务决定。
        repair = repair_used
        while True:
            payload = user_payload
            if feedback is not None:
                payload = {
                    "original_input": user_payload,
                    "original_instructions": system_prompt,
                    "validation_feedback": feedback,
                    "rejected_response": rejected_response,
                }
            instructions = _JSON_REPAIR_GUIDANCE + repair_prompt if repair else system_prompt
            # 格式编辑依据原结果与文字契约，不再识图或重新作出视觉判断。
            content = [] if repair else [{"type": "input_image", "image_url": uri} for uri in reference_images]
            content.append({"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)})
            input_items = [{"role": "user", "content": content}]
            budget = max_output_tokens
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
            budget = request_output_budget(provider.config, request)
            request["max_output_tokens"] = budget
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
                # 整个请求与 SDK 网络重试共用当前功能的调用时限。
                response = await call_with_retry(
                    provider.structured_response_client(repair=repair, before_submit=before_call),
                    config=provider.config,
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
            syntax_feedback: dict[str, str | int] | None = None
            parsed: Any = None
            try:
                try:
                    parsed = json.loads(strip_outer_code_fence(raw))
                except json.JSONDecodeError as error:
                    parsed = parse_llm_json(raw)
                    if parsed is None:
                        syntax_feedback = {
                            "message": error.msg,
                            "position": error.pos,
                            "line": error.lineno,
                            "column": error.colno,
                        }
                        raise LlmJsonValidationError("invalid_json") from None
                changed = tuple(
                    name
                    for name, value in preserved_fields.items()
                    if not isinstance(parsed, dict)
                    or name not in parsed
                    or type(parsed.get(name)) is not type(value)
                    or parsed[name] != value
                )
                if changed:
                    raise LlmJsonValidationError("decision_changed", fields=changed)
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
                    if isinstance(parsed, dict):
                        matching = [
                            choice
                            for choice in discriminator_choices
                            if all(
                                name not in parsed or (type(parsed[name]) is type(value) and parsed[name] == value)
                                for name, value in choice.items()
                            )
                        ]
                        if matching:
                            preserved_fields.update((name, parsed[name]) for name in matching[0] if name in parsed)
                        preserved_fields.update(
                            (name, parsed[name])
                            for name, values in explicit_choices.items()
                            if name in parsed
                            and any(type(parsed[name]) is type(value) and parsed[name] == value for value in values)
                        )
                    if preserved_fields:
                        feedback["preserved_fields"] = preserved_fields
                    if syntax_feedback is not None:
                        feedback["json_error"] = syntax_feedback
                    rejected_response = raw
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
    task: AiTaskKey,
    reference_images: tuple[str, ...],
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> str:
    """带实际图像的视觉推理；独立短会话解析配置，无可用模型时明确失败。"""
    if not reference_images or any(not uri for uri in reference_images):
        raise ValueError("visual reasoning requires readable reference images")
    async with SESSION_LOCAL() as chain_db:
        chain = await resolve_vision_chain(chain_db, user_id, task=task)
    if not chain:
        raise VisualReasoningError("视觉分析服务未配置，请联系管理员")
    content = [{"type": "input_image", "image_url": uri} for uri in reference_images]
    content.append({"type": "input_text", "text": user_payload})
    input_items = [{"role": "user", "content": content}]

    async def _describe(provider: ChatProvider) -> str:
        if before_submit is not None:
            await before_submit()
        response = await call_with_retry(
            provider.raw_client(),
            config=provider.config,
            **build_responses_kwargs(
                model=provider.config.model,
                instructions=system_prompt,
                input_items=input_items,
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
