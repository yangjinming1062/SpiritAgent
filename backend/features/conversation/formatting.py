import json

from infrastructure.text import format_local_iso, safe_json_loads
from pydantic import JsonValue
from runtime.presets import COMPANION_PRESET_ID
from sqlalchemy import (
    ColumnElement,
    and_,
    case,
    cast,
    column,
    false,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH, aggregate_order_by

from features.conversation.models import Conversation, Message
from features.conversation.replies import (
    CompanionReply,
    CompanionReplyInput,
    LinkBubble,
    LinkBubbleInput,
    MediaBubble,
    MediaBubbleInput,
    TextBubble,
)
from features.conversation.snapshots import MessageSnapshot

# 与 chat-runtime.ts 的分段和 JavaScript trim、chatDisplayText 保持一致。
_BLANK_LINE_SPLIT = r"\r?\n(?:[ \t]*\r?\n)+"
_DISPLAY_WHITESPACE = " \t\r\n\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_MEDIA_MARKER = rf"(?<![A-Za-z0-9_])MEDIA:[^{_DISPLAY_WHITESPACE}]*"


def _is_json_array(content: ColumnElement[str | None] = Message.content) -> ColumnElement[bool]:
    """排除畸形、非数组与 JSONB 无法表示的 \\u0000；调用方须用 CASE 保护强转。"""
    return and_(
        content.op("IS JSON", is_comparison=True)(literal_column("ARRAY")),
        func.pg_input_is_valid(content, "jsonb"),
    )


def message_contains_text(query: str) -> ColumnElement[bool]:
    """逐泡匹配台词，解码 JSON 转义并排除演绎字段；多模态行只匹配文本部分，不扫描附件地址。"""
    parts = (
        func.jsonb_array_elements(cast(Message.content, JSONB)).table_valued(column("value", JSONB)).render_derived()
    )
    text_match = parts.c.value["text"].astext.icontains(query, autoescape=True)
    bubble_match = select(1).select_from(parts).where(text_match).correlate(Message).exists()
    saved_reply = case(
        (
            and_(
                Message.reply_json.op("IS JSON", is_comparison=True)(literal_column("OBJECT")),
                func.pg_input_is_valid(Message.reply_json, "jsonb"),
            ),
            cast(Message.reply_json, JSONB),
        ),
        else_=literal_column("'{}'::jsonb"),
    )
    saved_bubbles = saved_reply.op("->", return_type=JSONB)("bubbles")
    link_parts = (
        func.jsonb_array_elements(
            case((func.jsonb_typeof(saved_bubbles) == "array", saved_bubbles), else_=literal_column("'[]'::jsonb")),
        )
        .table_valued(column("value", JSONB))
        .render_derived()
    )
    source_match = (
        select(1)
        .select_from(link_parts)
        .where(
            link_parts.c.value["type"].astext == "link",
            or_(
                link_parts.c.value["title"].astext.icontains(query, autoescape=True),
                link_parts.c.value["url"].astext.icontains(query, autoescape=True),
            ),
        )
        .correlate(Message)
        .exists()
    )
    text_part_match = (
        select(1)
        .select_from(parts)
        .where(
            or_(
                and_(parts.c.value["type"].astext == "input_text", text_match),
                and_(
                    parts.c.value["type"].astext == "input_audio",
                    parts.c.value["transcript"].astext.icontains(query, autoescape=True),
                ),
            ),
        )
        .correlate(Message)
        .exists()
    )
    # 非 JSON 数组的多模态行不按原文匹配，避免把 parts 结构当文本搜。
    jsonb_array = _is_json_array()
    return case(
        (and_(Message.content_type == "companion_reply", jsonb_array), or_(bubble_match, source_match)),
        (and_(Message.content_type == "multimodal_v1", jsonb_array), text_part_match),
        (Message.content_type.in_(("companion_reply", "multimodal_v1")), false()),
        else_=Message.content.icontains(query, autoescape=True),
    )


def _visible_message_text() -> ColumnElement[str | None]:
    """多模态正文只取字符串 input_text 并保留数组顺序，与 extractMessageContent 一致。"""
    parts = (
        func.jsonb_array_elements(cast(Message.content, JSONB))
        .table_valued(column("value", JSONB), with_ordinality="position")
        .render_derived()
    )
    joined = (
        select(func.string_agg(parts.c.value["text"].astext, aggregate_order_by("\n", parts.c.position)))
        .where(parts.c.value["type"].astext == "input_text", func.jsonb_typeof(parts.c.value["text"]) == "string")
        .correlate(Message)
        .scalar_subquery()
    )
    return case(
        (and_(Message.content_type == "multimodal_v1", _is_json_array()), joined),
        else_=Message.content,
    )


def _companion_user_bubble_count() -> ColumnElement[int]:
    """陪伴用户行分段去空；空正文或纯附件与界面一致，至少保留一泡。"""
    segments = (
        func.regexp_split_to_table(_visible_message_text(), _BLANK_LINE_SPLIT).table_valued("segment").render_derived()
    )
    return (
        select(func.greatest(func.count(), 1))
        .select_from(segments)
        .where(func.btrim(segments.c.segment, _DISPLAY_WHITESPACE) != "")
        .correlate(Message)
        .scalar_subquery()
    )


def visible_bubble_count() -> ColumnElement[int]:
    """已保存消息的聊天气泡数；查询须关联 Conversation，系统状态与工具记录不计。"""
    reply_bubbles = func.jsonb_array_length(cast(Message.content, JSONB))
    assistant_text = func.btrim(
        func.regexp_replace(_visible_message_text(), _MEDIA_MARKER, "", "g"),
        _DISPLAY_WHITESPACE,
    )
    has_media = case(
        (
            _is_json_array(Message.media_json),
            cast(Message.media_json, JSONB).path_exists(cast('strict $[*] ? (@.type() == "object")', JSONPATH)),
        ),
        else_=false(),
    )
    return case(
        (
            and_(
                Message.role == "user",
                Conversation.system_preset_id == COMPANION_PRESET_ID,
                Conversation.parent_id.is_(None),
                or_(Message.subtype.is_(None), Message.subtype == ""),
            ),
            _companion_user_bubble_count(),
        ),
        (Message.role == "user", 1),
        (
            and_(Message.role == "assistant", Message.content_type == "companion_reply"),
            case((_is_json_array(), reply_bubbles), else_=0),
        ),
        (and_(Message.role == "assistant", or_(assistant_text != "", has_media)), 1),
        else_=0,
    )


def _companion_context_items(message: Message | MessageSnapshot, *, dialogue_only: bool) -> list[dict[str, JsonValue]]:
    delivery = CompanionReply.from_saved_snapshot(message.reply_json or "", message.content or "")
    values = json.loads(message.content or "")
    for value, bubble in zip(values, delivery.bubbles, strict=True):
        if isinstance(bubble, TextBubble):
            value["text"] = bubble.text
    source = CompanionReplyInput.model_validate(values)
    media = {b.media_id: b for b in delivery.bubbles if isinstance(b, MediaBubble)}
    links = {b.topic_id: b for b in delivery.bubbles if isinstance(b, LinkBubble)}
    return [
        {**bubble.model_dump(), "status": media[bubble.media_id].status}
        if isinstance(bubble, MediaBubbleInput)
        else links[bubble.topic_id].model_dump()
        if isinstance(bubble, LinkBubbleInput)
        else bubble.model_dump(include={"type", "text"} if dialogue_only else None, exclude_unset=True)
        for bubble in source.root
    ]


def companion_context_content(message: Message | MessageSnapshot, *, dialogue_only: bool = False) -> str:
    return json.dumps(_companion_context_items(message, dialogue_only=dialogue_only), ensure_ascii=False)


def message_text(m: Message | MessageSnapshot) -> str:
    """提取可读内容；结构化回复保留逐泡数组，只移除演绎，不合并气泡。"""
    raw = (m.content or "").strip()
    content_type = m.content_type

    if not raw:
        return ""

    if content_type == "multimodal_v1":
        parsed = safe_json_loads(raw, default=None)
        if isinstance(parsed, list):
            return "\n".join(
                p.get("text", "") if p.get("type") == "input_text" else p.get("transcript", "")
                for p in parsed
                if isinstance(p, dict) and p.get("type") in {"input_text", "input_audio"}
            ).strip()
        return raw

    if content_type == "companion_reply":
        return companion_context_content(m, dialogue_only=True)
    return raw


def format_messages_compact(
    msgs: list[Message],
    *,
    char_cap: int | None = None,
    user_local_tz: str | None = None,
) -> str:
    """保留会话与消息来源、发言归属、时间和工具关联；正文换行不能伪装成另一条发言。"""
    records = []
    for msg in msgs:
        content: str | list[dict[str, JsonValue]]
        if msg.content_type == "companion_reply":
            if not (msg.content or "").strip() and not msg.tool_calls:
                continue
            source = _companion_context_items(msg, dialogue_only=True)
            content = []
            remaining = char_cap
            truncated = False
            for bubble in source:
                if remaining is not None and remaining <= 0:
                    truncated = True
                    break
                if bubble["type"] in {"image", "video", "link"}:
                    content.append(bubble)
                    continue
                original_text = bubble["text"]
                if not isinstance(original_text, str):
                    raise ValueError("Dialogue bubbles require text")
                dialogue = original_text[:remaining]
                content.append({"type": bubble["type"], "text": dialogue})
                truncated |= dialogue != original_text
                if remaining is not None:
                    remaining -= len(dialogue)
        else:
            text = message_text(msg)
            if not text and not msg.tool_calls:
                continue
            content = text[:char_cap]
            truncated = char_cap is not None and len(text) > char_cap
        record = {
            "session_id": str(msg.conversation_id),
            "message_id": str(msg.id),
            "role": msg.role,
            "created_at": format_local_iso(msg.created_at, user_local_tz)
            if user_local_tz is not None and msg.created_at
            else msg.created_at.isoformat()
            if msg.created_at
            else None,
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
