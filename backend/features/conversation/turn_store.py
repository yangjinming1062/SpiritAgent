import json
from typing import Any, Literal

from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.constants import ATTACHMENT_TYPE_AUDIO, ATTACHMENT_TYPE_VIDEO
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from runtime.events.emit import emit_ws_event
from runtime.events.queries import invalidate_history_events
from runtime.memory_scope import MemoryScope
from runtime.presets import COMPANION_PRESET_ID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.assets.lifecycle import enqueue_asset_cleanup
from features.assets.references import message_asset_paths
from features.conversation.attachments import prune_media_in_range
from features.conversation.context_window import CHECKPOINT_SUBTYPE
from features.conversation.models import Conversation, Message
from features.conversation.replacement import reply_replacement_target
from features.conversation.replies import CompanionReply, MediaBubble
from features.conversation.reply_media import apply_video_status, bind_reply_videos
from features.conversation.request_contracts import ChatAttachment, ChatMessageRequest
from features.conversation.snapshots import MessageSnapshot
from features.conversation.store_contracts import AssistantTurnData, CompressionInfo
from features.conversation.submission_models import PromptSubmission
from features.conversation.turn_access import message_snapshot
from features.media.queries import transfer_reply_videos
from features.media.turn_state import MediaArtifact
from features.topics.store import record_topic_usage

logger = get_logger(__name__)


def _tool_result_row(content: Any) -> tuple[str, Literal["text", "multimodal_v1"]]:
    """工具结果行的正文与类型；非字符串负载 JSON 编码后保存。"""
    if isinstance(content, str):
        return content, "text"
    text = json.dumps(content, ensure_ascii=False, default=str)
    # 多模态结果（截图等）是 part 数组，按 multimodal_v1 保存才能读回为输出数组，否则整段 base64 会变成文本。
    return text, "multimodal_v1" if isinstance(content, list) else "text"


def build_persisted_content(text: str, attachments: list[ChatAttachment] | None) -> tuple[str, str]:
    """纯文本返回 ``(text, "text")``；有附件时返回 ``multimodal_v1`` 的 Responses 形状 parts 数组 JSON。"""
    if not attachments:
        return text, "text"
    parts = [{"type": "input_text", "text": text}]
    for att in attachments:
        url = att.file_url
        if att.type == ATTACHMENT_TYPE_AUDIO:
            parts.append({"type": "input_audio", "audio_url": url, "format": url.rsplit(".", 1)[-1]})
        elif att.type == ATTACHMENT_TYPE_VIDEO:
            parts.append({"type": "input_video", "video_url": url})
        else:
            parts.append({"type": "input_image", "image_url": url})
    if media_types := [part["type"] for part in parts[1:]]:
        # 地址可能是整段 data URL，只记数量与类型。
        logger.info(
            "Persisting multimodal user message",
            extra={"media_count": len(media_types), "media_types": media_types},
        )
    return json.dumps(parts, ensure_ascii=False), "multimodal_v1"


async def persist_extra_user_messages(db: AsyncSession, conv_id: int, items: list[dict]) -> list[int]:
    """在运行最终消息轮次前，批量持久化前置 user 消息，返回按插入序的行 id。"""
    rows: list[Message] = []
    for item in items:
        db_content, db_content_type = build_persisted_content(item.get("text") or "", item.get("attachments"))
        row = Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type)
        db.add(row)
        rows.append(row)
    await db.commit()
    return [row.id for row in rows]


async def persist_user_message(db: AsyncSession, conv_id: int, message: ChatMessageRequest) -> int:
    """插入 user 角色 Message 行并提交，返回行 id。"""
    db_content, db_content_type = build_persisted_content(message.content, message.attachments)
    row = Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type)
    db.add(row)
    await db.commit()
    return row.id


async def persist_compression_checkpoint(
    db: AsyncSession,
    conv_id: int,
    info: CompressionInfo,
    *,
    notify_user_id: int | None = None,
) -> MessageSnapshot:
    """写入压缩检查点，下一轮历史从其覆盖边界之后读取；原消息保留，边界前无人引用的音视频随之清理。"""
    checkpoint = Message(
        conversation_id=conv_id,
        role="system",
        content=info.checkpoint_text,
        subtype=CHECKPOINT_SUBTYPE,
        summary_through_message_id=info.through_message_id,
        prompt_tokens=info.prompt_tokens,
        completion_tokens=info.completion_tokens,
    )
    db.add(checkpoint)
    if notify_user_id is not None:
        await db.flush()
        emit_ws_event(
            db,
            user_id=notify_user_id,
            event_type="compress.completed",
            payload={
                "session_id": str(conv_id),
                "subtype": checkpoint.subtype,
                "text": checkpoint.content,
                "message_id": checkpoint.id,
            },
        )
    await db.commit()
    if info.prune_before_message_id:
        await prune_media_in_range(db, conv_id, hi=info.prune_before_message_id)
        await db.commit()
    return message_snapshot(checkpoint)


async def persist_tool_results(conv_id: int, results: list[tuple[str, Any]]) -> None:
    async with session_scope() as db:
        for call_id, content in results:
            text, content_type = _tool_result_row(content)
            db.add(
                Message(
                    conversation_id=conv_id,
                    role="tool",
                    tool_call_id=call_id,
                    content=text,
                    content_type=content_type,
                ),
            )
        await db.commit()


async def persist_tool_call(conv_id: int, data: AssistantTurnData) -> None:
    async with session_scope() as db:
        db.add(
            Message(
                conversation_id=conv_id,
                role="assistant",
                content=None,
                tool_calls=json.dumps(data.tool_calls, ensure_ascii=False),
                reasoning_content=data.reasoning or None,
                prompt_tokens=data.prompt_tokens,
                completion_tokens=data.completion_tokens,
                turn_duration_ms=data.duration_ms,
                generation_origin_json=data.generation_origin.model_dump_json() if data.generation_origin else None,
            ),
        )
        await db.commit()


async def persist_assistant_message(
    conv_id: int,
    user_id: int,
    data: AssistantTurnData,
    media: list[dict[str, str]] | None,
) -> int:
    async with session_scope() as db:
        row = Message(
            conversation_id=conv_id,
            role="assistant",
            content=data.content or None,
            content_type="companion_reply" if data.reply is not None else "text",
            media_json=json.dumps(media, ensure_ascii=False) if media else None,
            reasoning_content=data.reasoning or None,
            reply_json=data.reply.model_dump_json() if data.reply else None,
            prompt_tokens=data.prompt_tokens,
            completion_tokens=data.completion_tokens,
            turn_duration_ms=data.duration_ms,
            generation_origin_json=data.generation_origin.model_dump_json() if data.generation_origin else None,
        )
        db.add(row)
        if data.reply is not None:
            await db.flush()
            await bind_reply_videos(db, row, data.reply, user_id)
            await record_topic_usage(
                db,
                MemoryScope(user_id, COMPANION_PRESET_ID),
                data.reply.used_topic_ids,
                row.id,
                authorized_at=data.reply.topic_context_at,
            )
        await db.commit()
        return row.id


async def replace_assistant_message(
    conv_id: int,
    user_id: int,
    old_message_id: int,
    data: AssistantTurnData,
    media: list[dict[str, str]] | None,
    *,
    request_id: str | None = None,
) -> int:
    """媒体绑定和终端行原子交接；锁顺序为用户源行、视频任务、回复行。"""
    if not data.content.strip() or data.tool_calls:
        raise ValueError("Replacement requires a complete final answer")
    async with user_asset_lock(user_id), session_scope() as db:
        target = await reply_replacement_target(db, user_id, str(conv_id), old_message_id)
        await db.execute(select(Message).where(Message.id == target.source_message_id).with_for_update())
        row = Message(
            conversation_id=conv_id,
            role="assistant",
            content=data.content,
            content_type="companion_reply" if data.reply is not None else "text",
            reasoning_content=data.reasoning or None,
            prompt_tokens=data.prompt_tokens,
            completion_tokens=data.completion_tokens,
            turn_duration_ms=data.duration_ms,
            generation_origin_json=data.generation_origin.model_dump_json() if data.generation_origin else None,
            reply_json=data.reply.model_dump_json() if data.reply else None,
        )
        db.add(row)
        await db.flush()
        jobs = await transfer_reply_videos(
            db,
            user_id=user_id,
            session_id=str(conv_id),
            from_message_id=old_message_id,
            to_message_id=row.id,
        )
        old = await db.scalar(
            select(Message)
            .where(Message.id == old_message_id, Message.conversation_id == conv_id)
            .with_for_update()
            .execution_options(populate_existing=True),
        )
        if old is None:
            raise ValueError("Previous reply is no longer available")
        reply = data.reply
        if reply is not None:
            old_reply = CompanionReply.model_validate_json(old.reply_json) if old.reply_json else None
            selected = {b.media_id for b in reply.bubbles if isinstance(b, MediaBubble)}
            if old_reply is not None and any(
                isinstance(b, MediaBubble) and b.media_id not in selected for b in old_reply.bubbles
            ):
                raise ValueError("Replacement must retain the previous media")
            jobs_by_id = {job.id: job for job in jobs}
            for bubble in reply.bubbles:
                if not isinstance(bubble, MediaBubble) or bubble.job_id not in jobs_by_id:
                    continue
                artifact = MediaArtifact(bubble.media_id, "video", bubble.goal_id, bubble.status, job_id=bubble.job_id)
                apply_video_status(artifact, jobs_by_id[bubble.job_id])
                bubble.status, bubble.url, bubble.error = artifact.status, artifact.url, artifact.error
            row.reply_json = reply.model_dump_json()
            await bind_reply_videos(db, row, reply, user_id)
            await record_topic_usage(
                db,
                MemoryScope(user_id, COMPANION_PRESET_ID),
                reply.used_topic_ids,
                row.id,
                authorized_at=reply.topic_context_at,
            )
        else:
            previous_media = json.loads(old.media_json) if old.media_json else []
            merged = [*previous_media, *(media or [])]
            row.media_json = (
                json.dumps(
                    list({json.dumps(entry, sort_keys=True): entry for entry in merged}.values()),
                    ensure_ascii=False,
                )
                if merged
                else None
            )
        released = message_asset_paths(old, user_id) - message_asset_paths(row, user_id)
        await invalidate_history_events(db, user_id, {old_message_id}, (), ())
        await enqueue_asset_cleanup(db, user_id, released)
        await db.delete(old)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conv_id, Conversation.user_id == user_id)
            .values(history_revision=Conversation.history_revision + 1),
        )
        if request_id is not None:
            await db.execute(
                update(PromptSubmission)
                .where(
                    PromptSubmission.user_id == user_id,
                    PromptSubmission.conversation_id == conv_id,
                    PromptSubmission.request_id == request_id,
                    PromptSubmission.replace_reply_message_id == old_message_id,
                )
                .values(result_message_id=row.id, status="completed", error=None),
            )
        await db.commit()
        return row.id
