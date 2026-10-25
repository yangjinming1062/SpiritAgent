"""会话历史读取与气泡统计；按 Message.id 正序重建消息，使工具结果可回填调用名称。"""

from infrastructure.assets.asset_store import client_asset_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.models import Conversation, Message
from features.conversation.replacement import replaceable_reply_expression
from features.conversation.replies import CompanionReply, MediaBubble
from features.conversation.reply_audio import client_reply_bubbles
from features.media.contracts import VideoJobSnapshot
from features.media.queries import get_video_jobs


async def count_conversation_bubbles(db: AsyncSession, conversation_id: int) -> int:
    return int(
        await db.scalar(select(Conversation.visible_message_count).where(Conversation.id == conversation_id)) or 0
    )


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
    before_id: int | None = None,
    latest: int | None = None,
    only_ids: list[int] | None = None,
) -> list[dict]:
    """按 Message.id 正序重建消息；支持向前分页、最近条数与指定消息集合。"""
    stmt = select(Message, replaceable_reply_expression(conv_id).label("replaceable_id")).where(
        Message.conversation_id == conv_id
    )
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    if only_ids is not None:
        stmt = stmt.where(Message.id.in_(only_ids))
    stmt = stmt.order_by(Message.id.desc()).limit(latest) if latest is not None else stmt.order_by(Message.id)
    rows = (await db.execute(stmt)).all()
    messages = [row.Message for row in rows]
    replaceable_id = rows[0].replaceable_id if rows else None
    # 降序取最近若干条后翻回正序：前向重建要求工具调用先于其结果处理
    if latest is not None:
        messages.reverse()

    replies = {
        msg.id: CompanionReply.from_saved_snapshot(msg.reply_json, msg.content or "")
        for msg in messages
        if msg.reply_json
    }
    pending_video_ids = {
        bubble.job_id
        for reply in replies.values()
        for bubble in reply.bubbles
        if isinstance(bubble, MediaBubble)
        and bubble.type == "video"
        and bubble.status == "pending"
        and bubble.job_id is not None
    }
    video_jobs: dict[int, VideoJobSnapshot] = {}
    if pending_video_ids:
        video_jobs = await get_video_jobs(db, messages[0].user_id, pending_video_ids)

    tool_name_by_call_id: dict[str, str] = {}
    result: list[dict] = []
    for msg in messages:
        item: dict = {"role": msg.role, "content": msg.content, "content_type": msg.content_type}
        if msg.subtype:
            item["subtype"] = msg.subtype
        if msg.media_json:
            media = msg.media_json or None
            if isinstance(media, list) and media:
                item["media"] = client_media_entries([e for e in media if isinstance(e, dict)])
        if msg.reply_json:
            reply = replies[msg.id]
            bubbles = client_reply_bubbles(reply)
            for bubble, client_bubble in zip(reply.bubbles, bubbles, strict=True):
                if not isinstance(bubble, MediaBubble) or bubble.status != "pending" or bubble.job_id is None:
                    continue
                job = video_jobs.get(bubble.job_id)
                if (
                    job is not None
                    and job.session_id == str(conv_id)
                    and job.reply_message_id == msg.id
                    and job.media_id == bubble.media_id
                    and job.cleanup_requested_at is None
                    and job.video_queue is not None
                ):
                    client_bubble["video_queue"] = job.video_queue.model_dump(mode="json")
            item["bubbles"] = bubbles
        if msg.reasoning_content:
            item["reasoning"] = msg.reasoning_content
        item["id"] = msg.id
        if msg.role == "assistant" and msg.tool_calls is None:
            item["can_replace_reply"] = msg.id == replaceable_id
        if msg.created_at is not None:
            item["timestamp"] = int(msg.created_at.timestamp() * 1000)

        if msg.tool_calls:
            calls = msg.tool_calls or None
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
