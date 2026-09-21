import json
from typing import Any

from components import CONTEXT_SUMMARY_HEADROOM_FACTOR, DEFAULT_LANGUAGE, SETTINGS, get_logger
from prompts.chat import CONTEXT_SUMMARY_PROMPTS

from services.infrastructure.llm import (
    approx_responses_tokens,
    build_responses_kwargs,
    call_with_retry,
)

logger = get_logger(__name__)


def _summary_prompt(language: str) -> str:
    lang = (language or "").strip().lower()
    return CONTEXT_SUMMARY_PROMPTS.get(lang, CONTEXT_SUMMARY_PROMPTS[DEFAULT_LANGUAGE])


def _pick_compressible_block(
    rest: list[dict[str, Any]],
    *,
    preserve_recent: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """挑选最旧的连续非 system 块进行压缩；保留最近的若干条消息不动。"""
    if len(rest) <= preserve_recent + 1:
        return [], rest
    block = rest[:-preserve_recent] if preserve_recent else rest
    keep = rest[-preserve_recent:] if preserve_recent else []
    return block, keep


async def _summarize_block(
    block: list[dict[str, Any]],
    *,
    client: Any,
    model: str,
    target_tokens: int,
    temperature: float | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str, bool, int, int]:
    """通过 Responses API 对输入项生成摘要；输出截断时保留原上下文。"""
    request = build_responses_kwargs(
        model=model,
        instructions=_summary_prompt(language),
        input_items=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {"target_tokens": target_tokens, "conversation_items": block},
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
            },
        ],
        temperature=temperature if temperature is not None else 0.0,
        max_output_tokens=target_tokens * CONTEXT_SUMMARY_HEADROOM_FACTOR,
    )
    response = await call_with_retry(client, **request)
    was_truncated = (
        response.status == "incomplete"
        and getattr(getattr(response, "incomplete_details", None), "reason", None) == "max_output_tokens"
    )
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    return response.output_text.strip(), was_truncated, prompt_tokens, completion_tokens


async def compress_history_if_needed(
    context: dict[str, Any],
    *,
    client: Any,
    model: str,
    context_length: int,
    enabled: bool | None = None,
    threshold_ratio: float | None = None,
    target_tokens: int | None = None,
    temperature: float | None = None,
    language: str = DEFAULT_LANGUAGE,
    current_tokens: int | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按需或强制压缩历史；成功返回压缩后的 Responses 上下文，失败或无需压缩返回原上下文。"""
    if not force:
        if enabled is None:
            enabled = SETTINGS.enable_context_compression
        if not enabled:
            return context, None

        threshold = threshold_ratio if threshold_ratio is not None else SETTINGS.context_compression_threshold
        tokens_to_check = (
            current_tokens
            if current_tokens is not None
            else approx_responses_tokens(context["instructions"], context["input"])
        )
        if context_length <= 0 or tokens_to_check < context_length * threshold:
            return context, None

    target = target_tokens if target_tokens is not None else SETTINGS.context_summary_target_tokens

    block, keep = _pick_compressible_block(context["input"])
    if not block:
        return context, None

    try:
        summary, was_truncated, prompt_tokens, completion_tokens = await _summarize_block(
            block,
            client=client,
            model=model,
            target_tokens=target,
            temperature=temperature,
            language=language,
        )
    except Exception as exc:
        logger.warning("context_compressor: summary call failed, leaving history unchanged", extra={"error": str(exc)})
        return context, None

    if was_truncated:
        logger.warning(
            "context_compressor: LLM hit max_tokens cap while summarizing, leaving history unchanged",
            extra={"message_count": len(block)},
        )
        return context, None

    if not summary:
        logger.info("context_compressor: LLM returned empty summary; leaving history unchanged")
        return context, None

    replaced_count = len(block)
    placeholder = {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": f"[Conversation summary — {replaced_count} earlier items compressed]\n\n{summary}",
            },
        ],
    }
    compressed: dict[str, Any] = {"instructions": context["instructions"], "input": [placeholder, *keep]}
    logger.info(
        "context_compressor: summarized messages into one summary",
        extra={
            "replaced_count": replaced_count,
            "input_tokens": approx_responses_tokens("", block),
            "output_tokens": approx_responses_tokens("", [placeholder]),
            "original_count": len(context["input"]),
            "new_count": len(compressed["input"]),
        },
    )
    return compressed, {
        "summary": summary,
        "replaced_count": replaced_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
