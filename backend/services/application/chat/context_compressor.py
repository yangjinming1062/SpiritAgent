import json
from dataclasses import dataclass
from typing import Any

from components import (
    CONTEXT_SUMMARY_HEADROOM_FACTOR,
    LLM_MAX_OUTPUT_TOKENS,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
)
from prompts.chat import COMPRESSION_CHECKPOINT_TITLE_TEXTS, CONTEXT_SUMMARY_PROMPTS

from services.infrastructure.llm import (
    approx_responses_tokens,
    build_responses_kwargs,
    call_with_retry,
)

logger = get_logger(__name__)


class CompressionFailedError(RuntimeError):
    """摘要调用失败、未完成或返回空摘要；历史保持不变。"""


@dataclass(frozen=True)
class CompressionInfo:
    summary: str
    # 检查点正文（标题行 + 摘要）：压缩当轮的上下文占位与持久化行逐字相同，后续回合读到的即当轮所见。
    checkpoint_text: str
    replaced_count: int
    prompt_tokens: int
    completion_tokens: int
    through_message_id: int
    prune_before_message_id: int | None


def _summary_items(block: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """摘要仅接收文字与媒体引用，不把未提供视觉内容的 base64 当作文字资料。"""
    items = []
    for item in block:
        # 多模态工具结果的媒体在 function_call_output 的 output。
        key = "output" if item.get("type") == "function_call_output" else "content"
        content = item.get(key)
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
        items.append({**item, key: parts})
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
    keep_start = len(rest) - preserve_recent
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


def _summary_input(block: list[dict[str, Any]], target_tokens: int) -> list[dict[str, Any]]:
    return [
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
    ]


def _fit_summary_block(
    block: list[dict[str, Any]],
    source_ids: list[int | None],
    *,
    context_length: int,
    target_tokens: int,
    language: str,
) -> list[dict[str, Any]]:
    """只总结预算内的完整原消息前缀；超长单条保留原文，不把截断资料当作完整覆盖。"""
    output_budget = max(LLM_MAX_OUTPUT_TOKENS, target_tokens * CONTEXT_SUMMARY_HEADROOM_FACTOR)
    input_budget = context_length - output_budget - max(1024, context_length // 20)
    instructions = resolve_prompt_text(CONTEXT_SUMMARY_PROMPTS, language)
    pending_calls: set[str] = set()
    boundaries = []
    for index, item in enumerate(block):
        call_id = item.get("call_id")
        if item.get("type") == "function_call" and call_id:
            pending_calls.add(call_id)
        elif item.get("type") == "function_call_output":
            pending_calls.discard(call_id)
        end = index + 1
        if not pending_calls and (end == len(block) or source_ids[index] != source_ids[end]):
            boundaries.append(end)
    lo, hi = 0, len(boundaries)
    while lo < hi:
        mid = (lo + hi) // 2
        tokens = approx_responses_tokens(instructions, _summary_input(block[: boundaries[mid]], target_tokens))
        if tokens <= input_budget:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        raise CompressionFailedError("first complete message exceeds summary input budget")
    return block[: boundaries[lo - 1]]


async def _summarize_block(
    block: list[dict[str, Any]],
    *,
    client: Any,
    model: str,
    target_tokens: int,
    temperature: float,
    language: str,
    context_length: int,
) -> tuple[str, bool, int, int]:
    """通过 Responses API 对输入项生成摘要；响应未完成时保留原上下文。"""
    request = build_responses_kwargs(
        model=model,
        instructions=resolve_prompt_text(CONTEXT_SUMMARY_PROMPTS, language),
        input_items=_summary_input(block, target_tokens),
        temperature=temperature,
        max_output_tokens=max(LLM_MAX_OUTPUT_TOKENS, target_tokens * CONTEXT_SUMMARY_HEADROOM_FACTOR),
    )
    response = await call_with_retry(client, context_length=context_length, **request)
    completed = response.status == "completed"
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "input_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "output_tokens", 0) if usage else 0
    return response.output_text.strip(), completed, prompt_tokens, completion_tokens


def compression_due(
    context: dict[str, Any],
    *,
    context_length: int,
    threshold_ratio: float,
    current_tokens: int | None,
) -> bool:
    """``current_tokens`` 为空时按当前上下文全量估算。"""
    tokens = (
        current_tokens
        if current_tokens is not None
        else approx_responses_tokens(context["instructions"], context["input"])
    )
    return context_length > 0 and tokens >= context_length * threshold_ratio


async def compress_history(
    context: dict[str, Any],
    *,
    client: Any,
    model: str,
    temperature: float,
    language: str,
    context_length: int,
) -> tuple[dict[str, Any], CompressionInfo | None]:
    """压缩可总结的历史前缀；成功返回压缩后的 Responses 上下文，无可压缩内容返回原上下文；摘要调用失败、未完成或为空时抛 CompressionFailedError，历史不变。"""
    target = SETTINGS.context_summary_target_tokens
    source_ids: list[int | None] = context["source_message_ids"]
    block, keep = _pick_compressible_block(context["input"], source_message_ids=source_ids)
    if not block:
        return context, None
    bounded = _fit_summary_block(
        block,
        source_ids,
        context_length=context_length,
        target_tokens=target,
        language=language,
    )
    keep = [*block[len(bounded) :], *keep]
    block = bounded
    through_id = source_ids[len(block) - 1]

    try:
        summary, completed, prompt_tokens, completion_tokens = await _summarize_block(
            block,
            client=client,
            model=model,
            target_tokens=target,
            temperature=temperature,
            language=language,
            context_length=context_length,
        )
    except Exception as exc:
        logger.warning("context_compressor: summary call failed, leaving history unchanged", exc_info=True)
        raise CompressionFailedError("summary call failed") from exc

    if not completed:
        logger.warning(
            "context_compressor: summary response did not complete, leaving history unchanged",
            extra={"input_item_count": len(block)},
        )
        raise CompressionFailedError("summary response did not complete")

    if not summary:
        logger.warning("context_compressor: LLM returned empty summary; leaving history unchanged")
        raise CompressionFailedError("summary response was empty")

    replaced_count = len(set(source_ids[: len(block)]))
    title = resolve_prompt_text(COMPRESSION_CHECKPOINT_TITLE_TEXTS, language).format(count=replaced_count)
    checkpoint_text = f"{title}\n{summary}"
    placeholder = {"role": "user", "content": [{"type": "input_text", "text": checkpoint_text}]}
    kept_ids = source_ids[len(block) :]
    compressed: dict[str, Any] = {
        "instructions": context["instructions"],
        "input": [placeholder, *keep],
        "source_message_ids": [through_id, *kept_ids],
        "checkpoint_indices": [0],
    }
    logger.info(
        "context_compressor: summarized history into one summary",
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
        checkpoint_text=checkpoint_text,
        replaced_count=replaced_count,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        through_message_id=through_id,
        prune_before_message_id=min((mid for mid in kept_ids if mid is not None), default=None),
    )
