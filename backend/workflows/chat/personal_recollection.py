"""只在说出口前校正回忆中的台词，媒体与已决定的表达方式沿用原交付。"""

import json
from typing import Any

from features.companion.personal_expression import ground_personal_lines
from features.conversation.replies import (
    CompanionReply,
    CompanionReplyInput,
    SpeechPerformance,
    TextBubble,
    VoiceBubble,
    VoiceBubbleInput,
)
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.text import safe_json_loads

from .reply_delivery import decode_companion_reply
from .reply_links import ReplyReferences

_PERSONAL_ARCHIVES = {"life_recall", "life_inspect", "diary_recall", "life_share_work"}


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


async def ground_recollection(
    config: UserLlmConfig, evidence: dict[str, Any], content: str, reply: CompanionReply
) -> tuple[str, CompanionReply]:
    source = CompanionReplyInput.model_validate_json(content)
    lines = [item.text for item in source.root if isinstance(item, (TextBubble, VoiceBubbleInput))]
    if not lines:
        return content, reply
    corrected = await ground_personal_lines(config, evidence, lines)
    if corrected == lines:
        return content, reply
    revised_raw = []
    revised_bubbles = []
    revisions = iter(corrected)
    for raw, bubble in zip(source.root, reply.bubbles, strict=True):
        if isinstance(raw, (TextBubble, VoiceBubbleInput)):
            line = next(revisions)
            if line is None:
                continue
            if raw.text != line:
                # 同一次校验处理口语分句和链接，校正不能引入新的媒体或朗读控制。
                envelope = json.dumps(
                    {"kind": "dialogue", "bubbles": [{"type": "text", "text": line}]}, ensure_ascii=False
                )
                parts, _, _ = decode_companion_reply(envelope, references=ReplyReferences.from_texts(tuple(lines)))
                for part in CompanionReplyInput.model_validate_json(parts).root:
                    assert isinstance(part, TextBubble)
                    if isinstance(bubble, VoiceBubble):
                        assert isinstance(raw, VoiceBubbleInput)
                        performance = SpeechPerformance.model_validate(
                            raw.speech.model_dump(exclude={"segments"}, exclude_none=True)
                        )
                        revised_raw.append(VoiceBubbleInput(type="voice", text=part.text, speech=performance))
                        revised_bubbles.append(
                            bubble.model_copy(
                                update={
                                    "text": part.text,
                                    "speech": performance.bind(bubble.speech.provider, bubble.speech.model),
                                    "audio": None,
                                }
                            )
                        )
                    else:
                        revised_raw.append(part)
                        revised_bubbles.append(part)
                continue
        revised_raw.append(raw)
        revised_bubbles.append(bubble)
    content = CompanionReplyInput(revised_raw).model_dump_json()
    result = reply.model_copy(update={"bubbles": revised_bubbles})
    result.validate_content(content)
    return content, result
