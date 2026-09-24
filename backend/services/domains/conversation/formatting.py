import json

from components import safe_json_loads
from modules.conversation import Message


def message_text(m: Message) -> str:
    """从 Message 行抽取纯文本；``content_type == "multimodal_v1"`` 行只返回 text part，避免 JSON 数组泄到 prompt 里。"""
    raw = (m.content or "").strip()

    if not raw or getattr(m, "content_type", "text") != "multimodal_v1":
        return raw

    parsed = safe_json_loads(raw, default=None)

    if not isinstance(parsed, list):
        return raw

    return "\n".join(
        p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") in {"input_text", "text"}
    ).strip()


def format_messages_compact(msgs: list[Message], *, char_cap: int | None = None) -> str:
    """保留发言归属、时间、截断与工具关联；正文中的换行不能伪装成另一条发言。"""
    records = []
    for msg in msgs:
        text = message_text(msg)
        if not text and not msg.tool_calls:
            continue
        record = {
            "role": msg.role,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "content": text[:char_cap],
            "truncated": char_cap is not None and len(text) > char_cap,
        }
        if msg.subtype:
            record["subtype"] = msg.subtype
        if msg.tool_calls:
            record["tool_calls"] = safe_json_loads(msg.tool_calls, default=[])
        if msg.tool_call_id:
            record["tool_call_id"] = msg.tool_call_id
        records.append(record)
    return json.dumps(records, ensure_ascii=False) if records else ""
