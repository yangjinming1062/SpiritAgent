import json
from dataclasses import dataclass
from typing import Any

from components import (
    CONTEXT_SUMMARY_HEADROOM_FACTOR,
    DEFAULT_LANGUAGE,
    LLM_MAX_OUTPUT_TOKENS,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
)
from prompts.chat import CONTEXT_SUMMARY_PROMPTS

from services.infrastructure.llm import (
    approx_responses_tokens,
    build_responses_kwargs,
    call_with_retry,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class CompressionInfo:
    summary: str
    replaced_count: int
    prompt_tokens: int
    completion_tokens: int
    through_message_id: int
    prune_before_message_id: int | None


def _summary_items(block: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """摘要仅接收文字与媒体引用，不把未提供视觉内容的 base64 当作文字资料。"""
    items = []
    for item in block:
        content = item.get("content")
        if not isinstance(content, list):
            items.append(item)
            continue
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("input_image", "input_video"):
                source = part.get("image_url") or part.get("video_url") or ""
                reference = source if isinstance(source, str) and not source.startswith("data:") else "inline media"
                parts.append(
                    {
                        "type": "input_text",
                        "text": f"[Media reference: {reference}; contents not supplied to this summary]",
                    },
                )
            else:
                parts.append(part)
        items.append({**item, "content": parts})
    return items


def _pick_compressible_block(
    rest: list[dict[str, Any]],
    *,
    source_message_ids: list[int | None],
    preserve_recent: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """压缩连续历史前缀，保留最近输入及无持久化来源的本轮资料。"""
    if len(rest) != len(source_message_ids):
        raise ValueError("Every context item requires a source message ID or an explicit runtime marker")
    if len(rest) <= preserve_recent + 1:
        return [], rest
    keep_start = len(rest) - preserve_recent if preserve_recent else len(rest)
    keep_start = min(keep_start, next((i for i, mid in enumerate(source_message_ids) if mid is None), len(rest)))
    call_positions = {
        item["call_id"]: index
        for index, item in enumerate(rest)
        if item.get("type") == "function_call" and item.get("call_id")
    }
    # 一条持久化消息可展开成多个输入项，工具批次也不可在 call/result 之间断开。
    while keep_start:
        previous_start = keep_start
        for item in rest[keep_start:]:
            if item.get("type") == "function_call_output":
                keep_start = min(keep_start, call_positions.get(item.get("call_id"), keep_start))
        if keep_start < len(source_message_ids):
            while keep_start and source_message_ids[keep_start - 1] == source_message_ids[keep_start]:
                keep_start -= 1
        if keep_start == previous_start:
            break
    return rest[:keep_start], rest[keep_start:]


async def _summarize_block(
    block: list[dict[str, Any]],
    *,
    client: Any,
    model: str,
    target_tokens: int,
    temperature: float | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str, bool, int, int]:
    """通过 Responses API 对输入项生成摘要；响应未完成时保留原上下文。"""
    request = build_responses_kwargs(
        model=model,
        instructions=resolve_prompt_text(CONTEXT_SUMMARY_PROMPTS, language),
        input_items=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {"target_tokens": target_tokens, "conversation_items": _summary_items(block)},
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
            },
        ],
        temperature=temperature if temperature is not None else 0.0,
        max_output_tokens=max(LLM_MAX_OUTPUT_TOKENS, target_tokens * CONTEXT_SUMMARY_HEADROOM_FACTOR),
    )
    response = await call_with_retry(client, **request)
    completed = response.status == "completed"
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    return response.output_text.strip(), completed, prompt_tokens, completion_tokens


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
) -> tuple[dict[str, Any], CompressionInfo | None]:
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

    source_ids: list[int | None] = context["source_message_ids"]
    block, keep = _pick_compressible_block(context["input"], source_message_ids=source_ids)
    if not block:
        return context, None
    through_id = source_ids[len(block) - 1]
    if through_id is None:
        raise ValueError("Compression requires an original message boundary")

    try:
        summary, completed, prompt_tokens, completion_tokens = await _summarize_block(
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

    if not completed:
        logger.warning(
            "context_compressor: summary response did not complete, leaving history unchanged",
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
    kept_ids = source_ids[replaced_count:]
    compressed: dict[str, Any] = {
        "instructions": context["instructions"],
        "input": [placeholder, *keep],
        "source_message_ids": [through_id, *kept_ids],
    }
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
    return compressed, CompressionInfo(
        summary=summary,
        replaced_count=replaced_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        through_message_id=through_id,
        prune_before_message_id=min((mid for mid in kept_ids if mid is not None), default=None),
    )
