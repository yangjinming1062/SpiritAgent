"""模块拥有的备份表、校验与恢复操作。"""

from collections import defaultdict
from datetime import datetime
from functools import partial
from typing import Any

from infrastructure.ai.selection import GenerationOrigin
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.backup import BackupContext, BackupRows, BackupTable, IdMap
from runtime.memory_scope import MemoryScope
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_releases import collect_message_asset_releases
from features.topics.schemas import TopicCardSnapshot
from features.topics.store import validate_restored_topic_cards

from .context_window import CHECKPOINT_SUBTYPE
from .memory_scope import validate_memory_scope
from .models import Conversation, Message
from .replies import CompanionReply, CompanionReplyInput, LinkBubble, LinkBubbleInput, MediaBubble


def _prepare(table: str, raw: dict[str, Any], payload: dict[str, Any], context: BackupContext) -> None:
    user_id = context.user_id
    if table == "messages" and payload.get("conversation_id") is None:
        raise ValueError("Message conversation is missing from backup")

    if table == "messages":
        if payload.get("generation_origin_json"):
            payload["generation_origin_json"] = GenerationOrigin.model_validate_json(
                payload["generation_origin_json"],
            ).model_dump_json()
        through_id = payload.get("summary_through_message_id")
        if payload.get("subtype") == CHECKPOINT_SUBTYPE:
            if type(through_id) is not int or through_id <= 0:
                raise ValueError("Conversation summary requires an original message boundary")
        elif through_id is not None:
            raise ValueError("Only summary messages may have a summary boundary")

    if table == "conversations":
        payload["parent_id"] = payload["forked_from_id"] = None
        if not isinstance(payload.get("context_after_message_id"), int) or payload["context_after_message_id"] < 0:
            raise ValueError("Invalid conversation context watermark")
        payload["context_after_message_id"] = 0
        payload["memory_reviewed_message_id"] = 0
        if payload.get("is_automation"):
            if payload.get("system_preset_id") != "automation":
                raise ValueError("Invalid automation preset")
        else:
            validate_memory_scope(MemoryScope(user_id, payload.get("system_preset_id")))

    if table == "messages" and payload.get("reply_json"):
        if payload.get("media_json"):
            raise ValueError("Structured replies cannot contain separate media attachments")
        reply = CompanionReply.model_validate_json(payload["reply_json"])
        reply.topic_context_at = None
        source = CompanionReplyInput.model_validate_json(payload.get("content") or "")
        topic_ids = set(reply.used_topic_ids)
        topic_ids.update(b.topic_id for b in reply.bubbles if isinstance(b, LinkBubble))
        topic_map = context.id_map.get("companion_topics", {})
        if any(str(topic_id) not in topic_map for topic_id in topic_ids):
            raise ValueError("A shared topic source is missing from backup")
        reply.used_topic_ids = [int(topic_map[str(topic_id)]) for topic_id in reply.used_topic_ids]
        for bubble in source.root:
            if isinstance(bubble, LinkBubbleInput):
                if str(bubble.topic_id) not in topic_map:
                    raise ValueError("A shared topic source is missing from backup")
                bubble.topic_id = int(topic_map[str(bubble.topic_id)])
        for bubble in reply.bubbles:
            if isinstance(bubble, LinkBubble):
                bubble.topic_id = int(topic_map[str(bubble.topic_id)])
            if isinstance(bubble, MediaBubble):
                bubble.job_id = None
                if bubble.status == "pending":
                    bubble.status, bubble.url, bubble.error = "failed", None, "恢复的生成任务不可继续，请重新提出请求"
        payload["content"] = source.model_dump_json()
        reply.validate_content(payload["content"])
        payload["reply_json"] = reply.model_dump_json()


async def restore_conversation_context(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    id_map: IdMap,
) -> None:
    messages = {str(row["id"]): row for row in rows.get("messages", [])}
    for original_id, raw in messages.items():
        if raw.get("subtype") != CHECKPOINT_SUBTYPE:
            continue
        source_id = str(raw["summary_through_message_id"])
        source = messages.get(source_id)
        if (
            source is None
            or str(source["conversation_id"]) != str(raw["conversation_id"])
            or source.get("subtype") == CHECKPOINT_SUBTYPE
        ):
            raise ValueError("Conversation summary boundary is missing from backup")
        checkpoint = await db.get(Message, int(id_map["messages"][original_id]))
        checkpoint.summary_through_message_id = int(id_map["messages"][source_id])
    by_conversation: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for message in sorted(messages.values(), key=lambda message: int(message["id"])):
        by_conversation[str(message["conversation_id"])].append(message)
    for raw in rows.get("conversations", []):
        original_id = str(raw["id"])
        ordered_messages = by_conversation[original_id]
        original_ids = [int(message["id"]) for message in ordered_messages]
        mapped_ids = [int(id_map["messages"][str(mid)]) for mid in original_ids]
        watermark = raw["context_after_message_id"]
        if watermark:
            conv = await db.get(Conversation, int(id_map["conversations"][original_id]))
            conv.context_after_message_id = max(
                (
                    mapped_id
                    for original, mapped_id in zip(original_ids, mapped_ids, strict=True)
                    if original <= watermark
                ),
                default=0,
            )
    await db.flush()


CONVERSATION_MERGE_CONFLICT_REASON = "固定会话已有历史，或任一侧上下文已清理，无法合并；请取消会话恢复或使用覆盖模式。"


async def validate_conversation_merge(db: AsyncSession, existing: Conversation, raw: dict[str, Any]) -> None:
    if (
        raw.get("context_after_message_id")
        or existing.context_after_message_id
        or await db.scalar(select(Message.id).where(Message.conversation_id == existing.id).limit(1)) is not None
    ):
        raise ValueError(CONVERSATION_MERGE_CONFLICT_REASON)


async def validate_special_merges(db: AsyncSession, user_id: int, rows: list[dict[str, Any]]) -> None:
    for raw in rows:
        if raw.get("kind") != "special" or not raw.get("system_preset_id"):
            continue
        existing = await db.scalar(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.kind == "special",
                Conversation.system_preset_id == raw["system_preset_id"],
            ),
        )
        if existing is not None:
            await validate_conversation_merge(db, existing, raw)


async def validate_automation_conversation(db: AsyncSession, conversation_id: int, user_id: int) -> None:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id or not conversation.is_automation:
        raise ValueError("Standard job requires an automation conversation")


async def _find_special(
    db: AsyncSession,
    raw: dict[str, Any],
    payload: dict[str, Any],
    context: BackupContext,
) -> int | None:
    if payload.get("kind") != "special" or not payload.get("system_preset_id"):
        return None
    await db.flush()
    existing = await db.scalar(
        select(Conversation).where(
            Conversation.user_id == context.user_id,
            Conversation.kind == "special",
            Conversation.system_preset_id == payload["system_preset_id"],
        ),
    )
    if existing is not None:
        await validate_conversation_merge(db, existing, raw)
        return existing.id
    return None


async def _lineages(
    db: AsyncSession,
    rows: list[dict[str, Any]],
    new_map: dict[str, int | str],
    context: BackupContext,
) -> None:
    for raw in rows:
        if ("conversations", str(raw["id"])) not in context.rewriter.inserted_rows:
            continue
        parent_id, forked_from_id = raw.get("parent_id"), raw.get("forked_from_id")
        if parent_id is None and forked_from_id is None:
            continue
        conversation = await db.get(Conversation, new_map[str(raw["id"])])
        if parent_id is not None:
            if str(parent_id) not in new_map:
                raise ValueError("Conversation parent is missing from backup")
            conversation.parent_id = new_map[str(parent_id)]
            parent = await db.get(Conversation, conversation.parent_id)
            if parent.user_id != context.user_id or parent.system_preset_id != conversation.system_preset_id:
                raise ValueError("Conversation parent belongs to a different scope")
        if forked_from_id is not None:
            conversation.forked_from_id = new_map.get(str(forked_from_id))
        await db.flush()
        if raw.get("updated_at") is not None:
            conversation.updated_at = datetime.fromisoformat(raw["updated_at"])


async def _clear(db: AsyncSession, user_id: int) -> None:
    conversation_ids = list(await db.scalars(select(Conversation.id).where(Conversation.user_id == user_id)))
    async with user_asset_lock(user_id):
        await collect_message_asset_releases(db, user_id, conversation_ids)
        await db.execute(delete(Conversation).where(Conversation.user_id == user_id))


async def _restore_context(db: AsyncSession, rows: BackupRows, context: BackupContext) -> None:
    await restore_conversation_context(db, rows, context.id_map)
    scope = MemoryScope(context.user_id, "companion")
    for raw in rows.get("messages", []):
        message_id = context.id_map.get("messages", {}).get(str(raw["id"]))
        if message_id is None:
            continue
        message = await db.get(Message, int(message_id))
        if message is None or not message.reply_json:
            continue
        reply = CompanionReply.model_validate_json(message.reply_json)
        cards = [
            TopicCardSnapshot.model_validate(bubble.model_dump())
            for bubble in reply.bubbles
            if isinstance(bubble, LinkBubble)
        ]
        if cards:
            await validate_restored_topic_cards(db, scope, cards)


def backup_tables() -> tuple[BackupTable, ...]:
    return (
        BackupTable(
            "conversations",
            Conversation,
            prepare_payload=partial(_prepare, "conversations"),
            find_existing=_find_special,
            after_rows=_lineages,
            custom_delete=_clear,
        ),
        BackupTable(
            "messages",
            Message,
            foreign_keys={"conversation_id": "conversations"},
            order_by_original_id=True,
            export_query=lambda user_id: select(Message).join(Conversation).where(Conversation.user_id == user_id),
            prepare_payload=partial(_prepare, "messages"),
            restore_context=_restore_context,
        ),
    )
