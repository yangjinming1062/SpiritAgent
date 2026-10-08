"""单次文本与视觉模型调用、终态检查和错误诊断。"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, get_logger, strip_outer_code_fence
from openai.types.responses import Response

from .error_classifier import ClassifiedError, FailoverReason, LlmCallBlockedError, LLMRuntimeError
from .llm_client import resolve_provider_chain, resolve_vision_chain
from .llm_fallback import execute_with_fallback
from .llm_retry import call_with_retry
from .providers import (
    ChatProvider,
    ProviderConfig,
    ServiceType,
    resolve_context_tokens,
    resolve_provider_reasoning_effort,
)
from .responses import approx_responses_tokens, build_responses_kwargs
from .user_config import UserLlmConfig

logger = get_logger(__name__)


class VisualReasoningError(RuntimeError):
    """视觉推理失败；异常文本可展示，供应商诊断只写入日志。"""


class IncompleteLlmResponseError(RuntimeError):
    """供应商响应未完成；异常只含状态、终止原因与错误码。"""


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
    if not text:
        raise RuntimeError("prompt enhancer returned an empty response")
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
    response = await call_with_retry(provider.raw_client(), **retry_kwargs, **request)
    if response.status != "completed":
        raise IncompleteLlmResponseError(f"LLM response not completed: {_response_failure_detail(response)}")
    return response.output_text


def _output_token_budget(
    context_length: int,
    instructions: str,
    input_items: list[dict[str, Any]],
    max_output_tokens: int,
) -> int:
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
    max_output_tokens: int,
    reasoning_effort: str | None = None,
    json_output: bool = False,
    temperature: float | None = None,
) -> str:
    """沿能力链执行单次非流式调用；温度按归一化刻度换算，推理档位按当前供应商支持集映射。"""
    user_content = (
        json.dumps(user_payload, ensure_ascii=False) if isinstance(user_payload, dict | list) else str(user_payload)
    )
    input_items = [{"role": "user", "content": [{"type": "input_text", "text": user_content}]}]

    async def call(provider: ChatProvider) -> str:
        context_length = resolve_context_tokens(provider.config)
        effort = resolve_provider_reasoning_effort(reasoning_effort, provider.REASONING_EFFORTS)
        request = build_responses_kwargs(
            model=provider.config.model,
            instructions=system_prompt,
            input_items=input_items,
            max_output_tokens=_output_token_budget(context_length, system_prompt, input_items, max_output_tokens),
            temperature=provider.scale_temperature(temperature) if temperature is not None else None,
            reasoning={"effort": effort} if effort else None,
            text={"format": {"type": "json_object"}} if json_output and provider.supports_json_object else None,
        )
        return await _call_text_response(
            provider,
            request,
            context_length=context_length,
        )

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
