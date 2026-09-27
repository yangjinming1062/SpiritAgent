import json

from components import safe_json_loads
from modules.conversation import CompanionReplyInput, Message
from sqlalchemy import ColumnElement, case, cast, column, func, select
from sqlalchemy.dialects.postgresql import JSONB


def message_contains_text(query: str) -> ColumnElement[bool]:
    """逐泡匹配台词，解码 JSON 转义并排除演绎字段。"""
    parts = (
        func.jsonb_array_elements(cast(Message.content, JSONB)).table_valued(column("value", JSONB)).render_derived()
    )
    bubble_match = (
        select(1)
        .select_from(parts)
        .where(parts.c.value["text"].astext.icontains(query, autoescape=True))
        .correlate(Message)
        .exists()
    )
    return case(
        (Message.content_type == "companion_reply", bubble_match),
        else_=Message.content.icontains(query, autoescape=True),
    )


def message_text(m: Message) -> str:
    """提取可读内容；结构化回复保留逐泡数组，只移除演绎，不合并气泡。"""
    raw = (m.content or "").strip()
    content_type = m.content_type

    if not raw:
        return ""

    if content_type == "multimodal_v1":
        parsed = safe_json_loads(raw, default=None)
        if isinstance(parsed, list):
            return "\n".join(
                p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") in {"input_text", "text"}
            ).strip()
        return raw

    if content_type == "companion_reply":
        source = CompanionReplyInput.model_validate_json(raw)
        return json.dumps([bubble.model_dump(include={"type", "text"}) for bubble in source.root], ensure_ascii=False)
    return raw


def format_messages_compact(msgs: list[Message], *, char_cap: int | None = None) -> str:
    """保留发言归属、时间、截断与工具关联；正文中的换行不能伪装成另一条发言。"""
    records = []
    for msg in msgs:
        text = message_text(msg)
        if not text and not msg.tool_calls:
            continue
        content: str | list[dict[str, str]] = text[:char_cap]
        truncated = char_cap is not None and len(text) > char_cap
        if msg.content_type == "companion_reply":
            source = CompanionReplyInput.model_validate_json(msg.content or "")
            content = []
            remaining = char_cap
            truncated = False
            for bubble in source.root:
                if remaining is not None and remaining <= 0:
                    truncated = True
                    break
                dialogue = bubble.text[:remaining]
                content.append({"type": bubble.type, "text": dialogue})
                truncated |= dialogue != bubble.text
                if remaining is not None:
                    remaining -= len(dialogue)
        record = {
            "role": msg.role,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "content": content,
            "truncated": truncated,
        }
        if msg.subtype:
            record["subtype"] = msg.subtype
        if msg.tool_calls:
            record["tool_calls"] = safe_json_loads(msg.tool_calls, default=[])
        if msg.tool_call_id:
            record["tool_call_id"] = msg.tool_call_id
        records.append(record)
    return json.dumps(records, ensure_ascii=False) if records else ""
