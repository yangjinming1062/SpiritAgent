"""只在说出口前校正回忆中的台词，媒体与已决定的表达方式沿用原交付。"""

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Literal

from features.companion.personal_expression import ground_personal_lines
from features.conversation.replies import (
    CompanionReply,
    CompanionReplyInput,
    SpeechPerformance,
    TextBubble,
    VoiceBubble,
    VoiceBubbleInput,
)
from infrastructure.ai.decision import DecisionOption, DecisionQuestion, choose_decisions, decision_input_fits
from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.prompt_engineer import LlmJsonValidationError
from infrastructure.ai.response_validation import IncompleteLlmResponseError
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.logger import get_logger
from infrastructure.redact import redact_sensitive_text
from infrastructure.text import safe_json_loads
from pydantic import JsonValue

from .reply_delivery import decode_companion_reply
from .reply_links import ReplyReferences

logger = get_logger(__name__)
_PERSONAL_ARCHIVES = {"life_recall", "life_inspect", "diary_recall", "life_share_work"}
# 控制待判断正文长度，余量用于跨批上下文和虚构语境判断。
_RECOLLECTION_BATCH_TOKENS = 800
_RECOLLECTION_NEIGHBOR_CHARS = 160
_RECOLLECTION_WAIT_SECONDS = 4.0
_CLAIM_QUESTION = DecisionQuestion(
    instructions=(
        "Does the speaker claim a specific event really happened to them or both people? Consider the current "
        "request and the surrounding dialogue. An event inside an explicitly fictional, quoted, or hypothetical "
        "scene is ordinary even in first person. Greetings, questions, plans, and responses to what the person just "
        "said are also ordinary. Judge whether the speaker claims a real event; leave verification to a separate step."
    ),
    options=(
        DecisionOption("review", "A line presents a specific past action, conversation, or shared event as real."),
        DecisionOption("ordinary", "No actual past event is asserted; the text may mention a fictional event."),
    ),
)
_FICTION_QUESTION = DecisionQuestion(
    instructions=(
        "按整段对白的含义分类。current_request 是对方当前的话，draft_dialogue 是说话人准备说出的台词。"
        "结合上下句判断指代、说话主体和故事或假设的范围。台词把具体往事、行为或早前交流当作自己实际经历或共同经历讲述，选 review。"
        "提到的往事只发生在小说、角色台词、举例、引用或假设里，选 fiction。"
        "问候、感受、提问、计划、一般看法或回应对方刚说的事，且无上述事实主张，选 ordinary。判断陈述类型，事实依据另行核对。"
    ),
    options=(
        DecisionOption("review", "说话人把往事当作自己真实经历的行为、与对方早前的交流或共同经历来陈述。"),
        DecisionOption("fiction", "往事只属于虚构故事、角色扮演、人物台词、引用、举例或假设。"),
        DecisionOption("ordinary", "日常交流，不声称自己的具体往事或和对方早前的共同经历。"),
    ),
    min_probability=0.80,
)
_RECOLLECTION_QUESTIONS = {"decision": _CLAIM_QUESTION, "context": _FICTION_QUESTION}
_TARGET_QUESTION = DecisionQuestion(
    instructions=(
        "Classify only target_dialogue, using context_before to resolve who is speaking, when and what they mean. "
        "Choose ordinary only if every target line is a question, greeting, current feeling, plan, hypothetical, "
        "or other statement that makes no concrete claim about the speaker's past or an earlier shared event. "
        "Choose review for any such past claim or when the references leave this uncertain."
    ),
    options=(
        DecisionOption("ordinary", "The target lines clearly make no claim about past experiences or conversations."),
        DecisionOption("review", "At least one target line presents or might present such a past claim."),
    ),
    min_probability=0.80,
)
_CORRECTION_GUIDANCE = (
    " Retain only links already present in the supplied lines. "
    "When preceding_reply is provided, use it to interpret references, quotations and the scope of imagined scenes. "
    "Verify actual experiences against the other evidence. Preserve dialogue framed as fiction or a hypothesis."
)


def _redacted_tail(text: str, limit: int) -> str:
    return (redact_sensitive_text(text) or "")[-limit:]


def _dialogue_fragments(text: str, fits: Callable[[str], bool]) -> Iterator[str]:
    if len(text) <= 1 or fits(text):
        yield text
        return
    middle = len(text) // 2
    # 只有单句超出预算时才切分。让边界的措辞在两份输入中都可见。
    overlap = min(32, middle // 2)
    yield from _dialogue_fragments(text[: middle + overlap], fits)
    yield from _dialogue_fragments(text[middle - overlap :], fits)


def _dialogue_batches(lines: Sequence[tuple[int, str]], fits: Callable[[str], bool]) -> Iterator[tuple[str, set[int]]]:
    draft = ""
    positions: set[int] = set()
    for index, line in lines:
        for fragment in _dialogue_fragments(redact_sensitive_text(line) or "", fits):
            combined = f"{draft}\n{fragment}" if draft else fragment
            if draft and not fits(combined):
                yield draft, positions
                draft, positions = fragment, {index}
            else:
                draft = combined
                positions.add(index)
    if draft:
        yield draft, positions


def _with_dialogue_neighbors(draft: str, before: str, after: str, fits: Callable[[str], bool]) -> str:
    before = before[-_RECOLLECTION_NEIGHBOR_CHARS:]
    after = after[:_RECOLLECTION_NEIGHBOR_CHARS]
    if not before and not after:
        return draft
    for parts in ((before, draft, after), (before, draft), (draft, after)):
        joined = "\n".join(part for part in parts if part)
        if fits(joined):
            return joined
    return draft


async def screen_recollection(
    lines: Sequence[tuple[int, str]], *, request: str, earlier_bubble: str = ""
) -> frozenset[int]:
    """用相邻台词消解指代。只返回当前拟交付批次内的气泡位置。"""
    if not lines:
        return frozenset()
    request_tail = _redacted_tail(request, 240)

    def state(draft: str) -> dict[str, JsonValue]:
        return {"current_request": request_tail, "draft_dialogue": draft}

    def fits_core(draft: str) -> bool:
        return decision_input_fits(
            state(draft),
            questions={"decision": _CLAIM_QUESTION},
            max_tokens=_RECOLLECTION_BATCH_TOKENS,
        )

    def fits_with_context(draft: str) -> bool:
        return decision_input_fits(state(draft), questions=_RECOLLECTION_QUESTIONS)

    batches = list(_dialogue_batches(lines, fits_core))
    earlier_bubble = redact_sensitive_text(earlier_bubble) or ""
    needs_review: set[int] = set()
    for index, (draft, positions) in enumerate(batches):
        before = ""
        if index:
            previous, previous_positions = batches[index - 1]
            if positions.isdisjoint(previous_positions):
                before = previous.rsplit("\n", 1)[-1]
        else:
            before = earlier_bubble
        after = ""
        if index + 1 < len(batches):
            following, following_positions = batches[index + 1]
            if positions.isdisjoint(following_positions):
                after = following.split("\n", 1)[0]
        contextual_draft = _with_dialogue_neighbors(draft, before, after, fits_with_context)
        choices = await choose_decisions(
            "recollection_review",
            state=state(contextual_draft),
            questions=_RECOLLECTION_QUESTIONS,
            wait_seconds=_RECOLLECTION_WAIT_SECONDS,
        )
        if "review" in (choices.get("decision"), choices.get("context")):
            needs_review.update(positions)
    return frozenset(needs_review)


async def _confirm_ordinary_lines(
    lines: Sequence[tuple[int, str]], positions: set[int], *, request: str, preceding: str
) -> frozenset[int]:
    """整批命中但证据核对失败时，逐个确认哪些气泡仍可交付。"""
    request_tail = _redacted_tail(request, 240)
    context = _redacted_tail(preceding, _RECOLLECTION_NEIGHBOR_CHARS)
    questions = {"decision": _TARGET_QUESTION}

    def state(target: str) -> dict[str, JsonValue]:
        return {"current_request": request_tail, "context_before": context, "target_dialogue": target}

    def fits(draft: str) -> bool:
        return decision_input_fits(state(draft), questions=questions, max_tokens=_RECOLLECTION_BATCH_TOKENS)

    confirmed: set[int] = set()
    for index, line in lines:
        redacted = redact_sensitive_text(line) or ""
        next_context = (context + "\n" + redacted)[-_RECOLLECTION_NEIGHBOR_CHARS:]
        if index in positions:
            for fragment in _dialogue_fragments(redacted, fits):
                choices = await choose_decisions(
                    "recollection_line",
                    state=state(fragment),
                    questions=questions,
                    wait_seconds=_RECOLLECTION_WAIT_SECONDS,
                )
                if choices.get("decision") != "ordinary":
                    break
                context = (context + "\n" + fragment)[-_RECOLLECTION_NEIGHBOR_CHARS:]
            else:
                confirmed.add(index)
        context = next_context
    return frozenset(confirmed)


def _current_tool_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    start = context.get("turn_tool_start")
    if start is None:
        indices = context.get("user_input_indices", ())
        start = max(indices) + 1 if indices else len(context["input"])
    return context["input"][start:]


def consulted_personal_archive(context: dict[str, Any]) -> bool:
    return any(
        item.get("type") == "function_call" and item.get("name") in _PERSONAL_ARCHIVES
        for item in _current_tool_items(context)
    )


def recollection_evidence(context: dict[str, Any]) -> dict[str, Any]:
    items = context["input"]
    current = _current_tool_items(context)
    web_calls = {
        item["call_id"]: item["name"]
        for item in current
        if item.get("type") == "function_call"
        and item.get("call_id")
        and item.get("name") in {"web_extract", "web_search"}
    }
    sources = []
    for item in current:
        if item.get("type") == "function_call_output" and (tool := web_calls.get(item.get("call_id"))):
            output = item["output"]
            result = safe_json_loads(output) if isinstance(output, str) else None
            sources.append(
                {"tool": tool, "result": result}
                if isinstance(result, dict)
                else {"tool": tool, "partial_output": output}
            )
    archives = {
        item.get("call_id")
        for item in items
        if item.get("type") == "function_call" and item.get("name") in _PERSONAL_ARCHIVES
    }
    excerpts = [
        {"text": str(item["output"])[:12000], "excerpted": len(str(item["output"])) > 12000}
        for item in items
        if item.get("type") == "function_call_output" and item.get("call_id") in archives
    ]
    user_indices = set(context.get("user_input_indices", ()))
    exchange = [
        {"role": item["role"], "text": part["text"][:2000], "excerpted": len(part["text"]) > 2000}
        for index, item in enumerate(items[-24:], start=max(0, len(items) - 24))
        if (item.get("role") == "assistant" or item.get("role") == "user" and index in user_indices)
        and isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    background = (
        context["contact_evidence"]
        if "contact_evidence" in context
        else {"own_records": context["personal_evidence"], "recent_dialogue": exchange}
    )
    return {**background, "archive_results": excerpts[-4:], "external_sources": sources}


def _restore_written_layout(layout: Sequence[str], revisions: Iterator[str | None]) -> str | None:
    parts = []
    for original in layout:
        text = original.strip()
        if not text:
            parts.append(original)
            continue
        replacement = next(revisions)
        if replacement is None:
            continue
        if replacement.splitlines() != [replacement]:
            raise LlmJsonValidationError("keep_one_written_line_per_edit", fields=("replacement",))
        parts.append(original.replace(text, replacement.strip(), 1))
    restored = "".join(parts)
    return restored if restored.strip() else None


async def ground_recollection(
    config: UserLlmConfig,
    evidence: dict[str, Any],
    content: str,
    reply: CompanionReply,
    *,
    request: str,
    kind: Literal["dialogue", "written"],
    keep_prefix: int = 0,
    optional_review_positions: set[int] | None = None,
    held_position: int | None = None,
) -> tuple[str, CompanionReply]:
    source = CompanionReplyInput.model_validate_json(content)
    spoken = [
        (index, item.text)
        for index, item in enumerate(source.root[keep_prefix:], start=keep_prefix)
        if isinstance(item, (TextBubble, VoiceBubbleInput))
    ]
    if not spoken:
        return content, reply
    lines = [line for _, line in spoken]
    layouts = [line.splitlines(keepends=True) for line in lines] if kind == "written" else []
    review_lines = [part.strip() for layout in layouts for part in layout if part.strip()] if layouts else lines
    references = ReplyReferences.from_texts(tuple(lines))
    deliverable: tuple[str, CompanionReply] | None = None
    preceding = _redacted_tail(
        "\n".join(b.text for b in source.root[:keep_prefix] if isinstance(b, (TextBubble, VoiceBubbleInput))), 2000
    )
    if preceding:
        evidence = {**evidence, "preceding_reply": preceding}

    def assemble(corrected: list[str | None]) -> tuple[tuple[str, CompanionReply] | None, bool]:
        if corrected == lines:
            return (content, reply), False
        revised_raw = source.root[:keep_prefix]
        revised_bubbles = reply.bubbles[:keep_prefix]
        revisions = iter(corrected)
        invalid = False
        for raw, bubble in zip(source.root[keep_prefix:], reply.bubbles[keep_prefix:], strict=True):
            if isinstance(raw, (TextBubble, VoiceBubbleInput)):
                line = next(revisions)
                if line is None:
                    continue
                if raw.text != line:
                    assert isinstance(bubble, (TextBubble, VoiceBubble))
                    try:
                        raw_parts, parts = _replacement_bubbles(raw, bubble, line, references, kind)
                    except ValueError:
                        invalid = True
                    else:
                        revised_raw.extend(raw_parts)
                        revised_bubbles.extend(parts)
                    continue
            revised_raw.append(raw)
            revised_bubbles.append(bubble)
        if not revised_bubbles:
            return None, invalid
        revised_content = CompanionReplyInput(revised_raw).model_dump_json(exclude_unset=True)
        result = reply.model_copy(update={"bubbles": revised_bubbles})
        result.validate_content(revised_content)
        return (revised_content, result), invalid

    def validate_delivery(corrected: list[str | None]) -> None:
        nonlocal deliverable
        if layouts:
            revisions = iter(corrected)
            corrected = [_restore_written_layout(layout, revisions) for layout in layouts]
        candidate, invalid = assemble(corrected)
        if candidate is not None:
            deliverable = candidate
        if invalid or candidate is None:
            code = "invalid_written_replacement" if kind == "written" else "invalid_spoken_replacement"
            raise LlmJsonValidationError(code, fields=("replacement",))

    form = (
        " Each supplied line is one nonblank line of the written work. Edit it in place, keeping its punctuation "
        "and verbatim quotations. Paragraph breaks and indentation are preserved on delivery."
        if kind == "written"
        else " Write replacements as spoken sentences using plain punctuation. Leave out parentheses, "
        "asterisks, line breaks and backslashes."
    )
    if len(lines) < len(reply.bubbles):
        form += " Other parts of this reply are retained if all supplied lines are dropped."
    try:
        await ground_personal_lines(
            config,
            evidence,
            review_lines,
            delivery_guidance=form + _CORRECTION_GUIDANCE,
            validate_delivery=validate_delivery,
        )
    except (IncompleteLlmResponseError, LLMRuntimeError, MissingLlmConfigError) as exc:
        if optional_review_positions is None:
            raise
        if deliverable is None:
            if len(optional_review_positions) > 1:
                candidates = {index for index in optional_review_positions if index != held_position}
                ordinary = await _confirm_ordinary_lines(spoken, candidates, request=request, preceding=preceding)
                optional_review_positions = optional_review_positions - ordinary
            deliverable, _ = assemble([None if index in optional_review_positions else line for index, line in spoken])
        if deliverable is None:
            raise
        logger.warning("Recollection review kept deliverable bubbles", extra={"error_type": type(exc).__name__})
    assert deliverable is not None
    return deliverable


def _replacement_bubbles(
    raw: TextBubble | VoiceBubbleInput,
    bubble: TextBubble | VoiceBubble,
    line: str,
    references: ReplyReferences,
    kind: Literal["dialogue", "written"],
) -> tuple[Sequence[TextBubble | VoiceBubbleInput], Sequence[TextBubble | VoiceBubble]]:
    # 沿用交付校验的分句和链接边界，再绑定原语音演绎。
    if not ReplyReferences.from_texts((line,)).sources <= references.sources:
        raise ValueError("A correction must preserve the supplied links")
    envelope = json.dumps({"kind": kind, "bubbles": [{"type": "text", "text": line}]}, ensure_ascii=False)
    split, _, _ = decode_companion_reply(envelope, references=references)
    parts = [TextBubble.model_validate(value) for value in json.loads(split)]
    if isinstance(raw, TextBubble):
        return parts, parts
    assert isinstance(bubble, VoiceBubble)
    performance = SpeechPerformance.model_validate(raw.speech.model_dump(exclude={"segments"}, exclude_none=True))
    style = performance.bind(bubble.speech.provider, bubble.speech.model)
    return (
        [VoiceBubbleInput(type="voice", text=part.text, speech=performance) for part in parts],
        [bubble.model_copy(update={"text": part.text, "speech": style, "audio": None}) for part in parts],
    )
