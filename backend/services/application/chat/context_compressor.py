import json
from typing import Any

from components import CONTEXT_SUMMARY_HEADROOM_FACTOR, DEFAULT_LANGUAGE, SETTINGS, get_logger

from services.infrastructure.llm.llm_retry import call_with_retry
from services.infrastructure.llm.responses import approx_responses_tokens, build_responses_kwargs

logger = get_logger(__name__)

_SUMMARY_PROMPTS: dict[str, str] = {
    "zh": (
        "你正在压缩一段用户与桌面伙伴之间的对话历史。"
        "该摘要将替换原始消息，作为后续对话的唯一上下文，"
        "因此需同时保留任务连续性与伙伴关系的关键信息。\n\n"
        "保留：\n"
        "  * 用户提出的所有目标、约束、决策和未解决的问题。\n"
        "  * 解决用户请求的工具结果（文件路径、命令输出、搜索发现、生成物）。\n"
        "  * 遇到的错误及恢复路径。\n"
        "  * 用户要求记住的代码片段、URL、标识符或产物。\n"
        "  * 影响后续互动的情感基调变化或伙伴人格时刻。\n\n"
        "省略：\n"
        "  * 纯装饰性的客套话和重复的澄清。\n"
        "  * 对未来轮次的推测。\n"
        "  * 关于对话本身的元评论。\n\n"
        "用 markdown 格式撰写摘要，语言与用户主要使用的语言保持一致。"
        "要具体——优先保留‘编辑了 config.yaml 设置压缩阈值’而非‘讨论了配置’。"
        "目标长度：300-800 字。"
    ),
    "en": (
        "You are compressing a portion of an ongoing conversation between a user "
        "and their desktop companion. This summary replaces the original messages "
        "as the sole context for the conversation going forward, so preserve "
        "everything that matters for both task continuity and the companion "
        "relationship.\n\n"
        "Preserve:\n"
        "  * All user-stated goals, constraints, decisions, and unresolved questions.\n"
        "  * Tool results that resolved the user's request (file paths, command "
        "output, search findings, generated artifacts).\n"
        "  * Errors encountered and the recovery path taken.\n"
        "  * Code snippets, URLs, identifiers, or artifacts the user asked to "
        "remember.\n"
        "  * Notable shifts in emotional tone or companion-persona moments that "
        "shape the ongoing interaction.\n\n"
        "Omit:\n"
        "  * Purely decorative pleasantries and repeated clarifications with no "
        "information value.\n"
        "  * Speculation about future turns.\n"
        "  * Meta-commentary about the conversation itself.\n\n"
        "Write the summary in markdown, in the same language the user "
        "predominantly used. Be specific \u2014 prefer 'edited config.yaml to set "
        "compression threshold' over 'discussed configuration'. Target length: "
        "300-800 words."
    ),
}


def _summary_prompt(language: str) -> str:
    lang = (language or "").strip().lower()
    return _SUMMARY_PROMPTS.get(lang, _SUMMARY_PROMPTS[DEFAULT_LANGUAGE])


def _pick_compressible_block(
    rest: list[dict[str, Any]],
    *,
    max_input_messages: int,
    preserve_recent: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """挑选最旧的连续非 system 块进行压缩；保留最近的若干条消息不动。"""
    if len(rest) <= preserve_recent + 1:
        return [], rest
    candidates = rest[:-preserve_recent] if preserve_recent else rest
    block = candidates[:max_input_messages]
    leftover = candidates[max_input_messages:]
    keep = leftover + rest[-preserve_recent:] if preserve_recent else leftover
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
                        "text": f"Summarize this conversation history. Target: ~{target_tokens} tokens.\n\n{json.dumps(block, ensure_ascii=False, default=str)}",
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
    max_input_messages: int | None = None,
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
    cap = max_input_messages if max_input_messages is not None else SETTINGS.context_summary_max_input_messages

    block, keep = _pick_compressible_block(context["input"], max_input_messages=cap)
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
