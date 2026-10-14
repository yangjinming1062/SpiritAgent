import json
from typing import Any, Literal

from infrastructure.constants import ATTACHMENT_TYPE_AUDIO, ATTACHMENT_TYPE_VIDEO
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from runtime.events.emit import emit_ws_event
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.attachments import prune_media_in_range
from features.conversation.context_window import CHECKPOINT_SUBTYPE
from features.conversation.models import Message
from features.conversation.reply_media import bind_reply_videos
from features.conversation.request_contracts import ChatAttachment, ChatMessageRequest
from features.conversation.snapshots import MessageSnapshot
from features.conversation.store_contracts import AssistantTurnData, CompressionInfo
from features.conversation.turn_access import message_snapshot

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
        )
        db.add(row)
        if data.reply is not None:
            await db.flush()
            await bind_reply_videos(db, row, data.reply, user_id)
        await db.commit()
        return row.id
