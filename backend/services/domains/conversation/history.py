"""会话历史读取与气泡统计；按 Message.id 正序重建消息，使工具结果可回填调用名称。"""

from components import safe_json_loads
from modules.conversation import CompanionReply, Conversation, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.assets import client_asset_url

from .formatting import visible_bubble_count
from .reply_audio import client_reply_bubbles


async def count_conversation_bubbles(db: AsyncSession, conversation_id: int) -> int:
    return (
        await db.execute(
            select(func.coalesce(func.sum(visible_bubble_count()), 0))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.conversation_id == conversation_id),
        )
    ).scalar_one()


def client_media_entries(media: list[dict[str, str]]) -> list[dict[str, str]]:
    """把媒体条目里的裸资产路径改写为客户端可鉴权加载的 /api/companion/asset/ URL；其余形态原样保留。"""
    return [
        {
            **entry,
            **{
                key: client_asset_url(entry[key])
                for key in ("url", "audio_url")
                if isinstance(entry.get(key), str) and entry[key]
            },
        }
        for entry in media
    ]


async def build_session_messages(
    conv_id: int,
    db: AsyncSession,
    *,
    after_id: int | None = None,
    before_id: int | None = None,
    latest: int | None = None,
    only_ids: list[int] | None = None,
) -> list[dict]:
    """按时间正序重建会话消息列表；``after_id`` 只取其后的消息，``latest`` 只取最近的若干条，二者互斥。"""
    if after_id is not None and latest is not None:
        raise ValueError("after_id and latest are mutually exclusive")
    if after_id is not None and before_id is not None:
        raise ValueError("after_id and before_id are mutually exclusive")
    stmt = select(Message).where(Message.conversation_id == conv_id)
    if after_id is not None:
        stmt = stmt.where(Message.id > after_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    if only_ids is not None:
        stmt = stmt.where(Message.id.in_(only_ids))
    stmt = stmt.order_by(Message.id.desc()).limit(latest) if latest is not None else stmt.order_by(Message.id)
    messages = list((await db.execute(stmt)).scalars().all())
    # 降序取最近若干条后翻回正序：前向重建要求工具调用先于其结果处理
    if latest is not None:
        messages.reverse()

    tool_name_by_call_id: dict[str, str] = {}
    result: list[dict] = []
    for msg in messages:
        item: dict = {"role": msg.role, "content": msg.content, "content_type": msg.content_type}
        if msg.subtype:
            item["subtype"] = msg.subtype
        if msg.media_json:
            media = safe_json_loads(msg.media_json, default=None)
            if isinstance(media, list) and media:
                item["media"] = client_media_entries([e for e in media if isinstance(e, dict)])
        if msg.reply_json:
            reply = CompanionReply.model_validate_json(msg.reply_json)
            reply.validate_content(msg.content or "")
            item["bubbles"] = client_reply_bubbles(reply)
        if msg.reasoning_content:
            item["reasoning"] = msg.reasoning_content
        item["id"] = msg.id
        if msg.created_at is not None:
            item["timestamp"] = int(msg.created_at.timestamp() * 1000)

        if msg.tool_calls:
            calls = safe_json_loads(msg.tool_calls, default=None)
            if isinstance(calls, list):
                item["tool_calls"] = calls
                if msg.role == "assistant":
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = call.get("call_id")
                        name = call.get("name")
                        if isinstance(call_id, str) and isinstance(name, str) and name:
                            tool_name_by_call_id[call_id] = name
            else:
                item["tool_calls"] = msg.tool_calls

        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id
        if msg.role == "tool" and msg.tool_call_id:
            item["tool_name"] = tool_name_by_call_id.get(msg.tool_call_id, "")

        result.append(item)
    return result
