"""会话入口使用的查询与修改用例；ORM 行不离开所属模块。"""

import asyncio
import json
from typing import Any

from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.attachments import gc_session as attachments_gc_session
from infrastructure.constants import SESSION_HISTORY_PRE_BUFFER, SESSION_HISTORY_TRUNCATE_THRESHOLD
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.conversation import LastSubmissionSnapshot
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_releases import collect_message_asset_releases
from features.conversation.attachments import copy_forked_media_attachments, prune_media_in_range
from features.conversation.edit import replace_user_message
from features.conversation.fork import fork_conversation_from_message
from features.conversation.formatting import message_text
from features.conversation.history import build_session_messages, count_conversation_bubbles
from features.conversation.main_conversation import CLEARED_STATUS_SUBTYPE, get_or_create_special_conversation
from features.conversation.models import Conversation, Message
from features.conversation.replacement import ReplyReplacement, reply_replacement_target
from features.conversation.reply_audio import cancel_reply_audio
from features.conversation.retry import ReplyRetryNotAllowedError, get_reply_retry_message
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.submission_models import PromptSubmission
from features.conversation.turn_access import conversation_snapshot
from features.conversation.undo import resolve_undo_target, undo_conversation_to_message
from features.media.video_jobs import cleanup_user_media

logger = get_logger(__name__)


class ConversationNotFoundError(LookupError):
    pass


class ForkAttachmentError(RuntimeError):
    pass


async def _owned(db: AsyncSession, user_id: int, session_id: str) -> Conversation:
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise ConversationNotFoundError(f"session not found: {session_id!r}")
    return conv


async def get_conversation(user_id: int, session_id: str) -> ConversationSnapshot:
    async with SESSION_LOCAL() as db:
        return conversation_snapshot(await _owned(db, user_id, session_id))


async def ensure_main_conversation(user_id: int) -> ConversationSnapshot:
    async with SESSION_LOCAL() as db:
        return conversation_snapshot(await get_or_create_special_conversation(db, user_id, "companion"))


async def create_conversation(user_id: int, preset_id: str) -> ConversationSnapshot:
    async with SESSION_LOCAL() as db:
        conv = Conversation(user_id=user_id, system_preset_id=preset_id)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conversation_snapshot(conv)


async def fork_conversation(user_id: int, session_id: str, source_message_id: int) -> tuple[ConversationSnapshot, int]:
    async with SESSION_LOCAL() as db:
        result = await fork_conversation_from_message(db, user_id, session_id, source_message_id)
        conv = await _owned(db, user_id, result.session_id)
        conv_id = conv.id
        try:
            await copy_forked_media_attachments(db, str(conv.forked_from_id), str(conv_id))
        except Exception as exc:
            logger.exception("session.fork video copy failed", extra={"new_session_id": conv_id})
            await _discard_forked_conversation(db, conv_id)
            raise ForkAttachmentError("音视频附件复制失败，请稍后重试") from exc
        except asyncio.CancelledError:
            await asyncio.shield(_discard_forked_conversation(db, conv_id))
            raise
        return conversation_snapshot(conv), result.message_count


async def history_snapshot(
    conversation_id: int,
) -> tuple[list[dict[str, Any]], bool, str | None, int]:
    async with SESSION_LOCAL() as db:
        messages, truncated, next_cursor = await _fetch_truncated_history(conversation_id, db)
        return messages, truncated, next_cursor, await count_conversation_bubbles(db, conversation_id)


async def history_page(user_id: int, session_id: str, before_id: int | None, limit: int) -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        conv = await _owned(db, user_id, session_id)
        if (
            before_id is not None
            and await db.scalar(select(Message.id).where(Message.id == before_id, Message.conversation_id == conv.id))
            is None
        ):
            raise ValueError("History cursor is unavailable")
        rows = await build_session_messages(conv.id, db, before_id=before_id, latest=limit + 1)
    has_more = len(rows) > limit
    messages = rows[-limit:]
    return {
        "messages": messages,
        "next_cursor": messages[0]["id"] if has_more and messages else None,
        "has_more": has_more,
    }


async def last_submission(user_id: int, conversation_id: int) -> LastSubmissionSnapshot | None:
    async with SESSION_LOCAL() as db:
        row = await db.scalar(
            select(PromptSubmission)
            .where(PromptSubmission.user_id == user_id, PromptSubmission.conversation_id == conversation_id)
            .order_by(PromptSubmission.id.desc())
            .limit(1),
        )
        if row is None:
            return None
        ids = json.loads(row.message_ids_json)
        retry_id = None
        if row.status == "failed" and ids and row.replace_reply_message_id is None:
            try:
                await get_reply_retry_message(db, user_id, str(conversation_id), ids[-1])
                retry_id = ids[-1]
            except ReplyRetryNotAllowedError:
                pass
        return LastSubmissionSnapshot(
            row.request_id,
            row.status,
            row.error,
            ids,
            retry_id,
            row.replace_reply_message_id,
            row.result_message_id,
        )


async def retry_message_text(user_id: int, session_id: str, message_id: int) -> str:
    async with SESSION_LOCAL() as db:
        return message_text(await get_reply_retry_message(db, user_id, session_id, message_id))


async def replacement_reply_input(user_id: int, session_id: str, message_id: int) -> ReplyReplacement:
    async with SESSION_LOCAL() as db:
        return await reply_replacement_target(db, user_id, session_id, message_id)


async def edit_message_input(
    user_id: int,
    session_id: str,
    conversation_id: int,
    message_id: int,
    text: str,
) -> tuple[int, list[dict[str, Any]]]:
    async with SESSION_LOCAL() as db:
        replacement = await replace_user_message(db, user_id, session_id, message_id, text)
        await cleanup_user_media(user_id)
        return replacement.id, await build_session_messages(conversation_id, db)


async def clear_history(user_id: int, session_id: str) -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        return await _clear_history_in_session(db, await _owned(db, user_id, session_id))


async def undo_history(user_id: int, session_id: str, message_id: int) -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        conv = await resolve_undo_target(db, user_id, session_id, message_id)
        await prune_media_in_range(db, conv.id, lo=message_id)
        result = await undo_conversation_to_message(db, conv, message_id)
        await cleanup_user_media(user_id)
        return result.model_dump(mode="json")


async def _discard_forked_conversation(db: AsyncSession, conv_id: int) -> None:
    """派生后续步骤失败时撤销新会话：删除会话行（消息级联）和已写入的附件目录，不留下半成品。"""
    await db.rollback()
    conv = await db.get(Conversation, conv_id)
    if conv is None:
        return
    async with user_asset_lock(conv.user_id):
        removed = await collect_message_asset_releases(db, conv.user_id, [conv_id])
        await db.execute(delete(Conversation).where(Conversation.id == conv_id))
        await db.commit()
    await cancel_reply_audio(conv.user_id, removed)
    await cleanup_user_media(conv.user_id)
    try:
        await asyncio.to_thread(attachments_gc_session, str(conv_id))
    except OSError:
        logger.warning("attachments_gc_session failed for forked session %s", conv_id, exc_info=True)


async def _clear_history_in_session(db: AsyncSession, conv: Conversation) -> dict[str, Any]:
    """清空会话所有消息（含各 role，保留会话行 + 写一条 status_cleared 标记）；返回 {"session_id", "cleared_count", "messages"}，cleared_count 为真实删除行数。"""
    total = (
        await db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv.id),
        )
    ).scalar_one()

    # 临时上传视频的独立提交先完成，不能提前提交正式资源回收登记。
    await prune_media_in_range(db, conv.id)
    # 删除业务引用与回收登记同事务；提交以后再取消任务和删除文件。
    async with user_asset_lock(conv.user_id):
        removed = await collect_message_asset_releases(db, conv.user_id, [conv.id])
        await db.execute(delete(Message).where(Message.conversation_id == conv.id))
        marker = Message(
            conversation_id=conv.id,
            role="system",
            content=f"[🧹 会话已清空 — {total} 条消息]",
            subtype=CLEARED_STATUS_SUBTYPE,
        )
        db.add(marker)
        await db.commit()
    await cancel_reply_audio(conv.user_id, removed)
    await cleanup_user_media(conv.user_id)

    delivered = await build_session_messages(conv.id, db)
    return {
        "session_id": str(conv.id),
        "cleared_count": total,
        "messages": delivered,
    }


async def _fetch_truncated_history(conv_id: int, db: AsyncSession) -> tuple[list[dict[str, Any]], bool, str | None]:
    head = await build_session_messages(
        conv_id,
        db,
        latest=SESSION_HISTORY_TRUNCATE_THRESHOLD + SESSION_HISTORY_PRE_BUFFER,
    )
    truncated = len(head) > SESSION_HISTORY_TRUNCATE_THRESHOLD
    delivered = head[-SESSION_HISTORY_TRUNCATE_THRESHOLD:] if truncated else head
    next_cursor = str(delivered[0]["id"]) if truncated and delivered else None
    return delivered, truncated, next_cursor


async def all_messages(conversation_id: int) -> list[dict[str, Any]]:
    async with SESSION_LOCAL() as db:
        return await build_session_messages(conversation_id, db)
