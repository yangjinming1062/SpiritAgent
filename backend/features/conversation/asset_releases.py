"""删除历史时同事务释放消息、制作任务及待投递通知的引用。"""

from collections.abc import Iterable

from runtime.events.queries import invalidate_history_events
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import asset_paths, message_asset_paths
from features.media.queries import invalidate_conversation_video_jobs
from features.media.voice_queries import custom_voice_tokens, voice_asset_references

from .models import Conversation, Message


async def collect_message_asset_releases(
    db: AsyncSession,
    user_id: int,
    conversation_ids: Iterable[int],
    *,
    from_message_id: int | None = None,
) -> set[int]:
    conversations = set(conversation_ids)
    session_ids = {str(conversation_id) for conversation_id in conversations}
    query = (
        select(Message)
        .join(Conversation)
        .where(Conversation.user_id == user_id, Message.conversation_id.in_(conversations))
    )
    if from_message_id is not None:
        query = query.where(Message.id >= from_message_id)
    # 与入队共享源用户消息锁；终态交付按作业→回复行加锁。
    await db.execute(query.where(Message.role == "user").order_by(Message.id).with_for_update())
    messages = list(await db.scalars(query))
    message_ids = {message.id for message in messages}
    if not message_ids and from_message_id is not None:
        return set()
    released = set().union(*(message_asset_paths(message, user_id) for message in messages))
    released |= await voice_asset_references(
        db,
        user_id,
        set().union(*(custom_voice_tokens(message.reply_json) for message in messages)),
    )
    jobs = await invalidate_conversation_video_jobs(
        db,
        user_id,
        message_ids,
        session_ids if from_message_id is None else (),
    )
    released |= asset_paths([reference.model_dump() for reference in jobs.references], user_id)
    payloads = await invalidate_history_events(
        db,
        user_id,
        message_ids,
        {str(job_id) for job_id in jobs.job_ids},
        session_ids if from_message_id is None else (),
    )
    released |= asset_paths(payloads, user_id)
    await enqueue_asset_cleanup(db, user_id, released)
    await db.flush()
    return message_ids
