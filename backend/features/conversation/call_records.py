"""通话标记与独立的只读文字投影。"""

from datetime import datetime, timedelta
from typing import Literal

from infrastructure.api_ids import ResourceId
from infrastructure.database import session_scope
from pydantic import BaseModel
from runtime.events.emit import emit_ws_event
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.formatting import message_text
from features.conversation.models import CALL_RECORD_SUBTYPE, Message
from features.conversation.replies import CompanionReply, LinkBubble, TextBubble, VoiceBubble


class CallUtterance(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class CallRecordPage(BaseModel):
    duration_ms: int
    started_at: datetime
    ended_at: datetime
    utterances: list[CallUtterance]
    next_cursor: ResourceId | None


async def start_call_record(user_id: int, conversation_id: int, started_at: datetime) -> int:
    async with session_scope() as db:
        row = Message(
            user_id=user_id,
            conversation_id=conversation_id,
            role="system",
            subtype=CALL_RECORD_SUBTYPE,
            created_at=started_at,
            call_last_seen_at=started_at,
        )
        db.add(row)
        await db.commit()
        return row.id


async def observe_call(record_id: int, at: datetime) -> None:
    async with session_scope() as db:
        await db.execute(
            update(Message)
            .where(Message.id == record_id, Message.call_duration_ms.is_(None), Message.call_last_seen_at < at)
            .values(call_last_seen_at=at)
        )
        await db.commit()


def _finalize(db: AsyncSession, row: Message, ended_at: datetime, duration_ms: int) -> None:
    row.call_last_seen_at = ended_at
    row.call_duration_ms = duration_ms
    payload = {"session_id": str(row.conversation_id), "record_id": row.id}
    emit_ws_event(db, user_id=row.user_id, event_type="session.call_recorded", payload=payload)
    emit_ws_event(
        db, user_id=row.user_id, event_type="session.list_changed", payload={"session_id": payload["session_id"]}
    )


async def finish_call_record(record_id: int, duration_ms: int) -> None:
    async with session_scope() as db:
        row = await db.get(Message, record_id, with_for_update=True)
        if row is not None and row.call_duration_ms is None and row.subtype == CALL_RECORD_SUBTYPE:
            _finalize(db, row, row.created_at + timedelta(milliseconds=duration_ms), duration_ms)
            await db.commit()


async def recover_call_records() -> None:
    async with session_scope() as db:
        for row in await db.scalars(
            select(Message)
            .where(Message.subtype == CALL_RECORD_SUBTYPE, Message.call_duration_ms.is_(None))
            .with_for_update()
        ):
            assert row.call_last_seen_at is not None
            duration_ms = max(0, int((row.call_last_seen_at - row.created_at).total_seconds() * 1000))
            _finalize(db, row, row.call_last_seen_at, duration_ms)
        await db.commit()


async def read_call_record(
    user_id: int, conversation_id: int, record_id: int, *, after_id: int | None, limit: int
) -> CallRecordPage | None:
    async with session_scope() as db:
        row = await db.scalar(
            select(Message)
            .where(
                Message.id == record_id,
                Message.user_id == user_id,
                Message.conversation_id == conversation_id,
                Message.subtype == CALL_RECORD_SUBTYPE,
                Message.call_duration_ms.is_not(None),
            )
            .with_for_update(read=True, key_share=True)
        )
        if row is None or row.call_duration_ms is None or row.call_last_seen_at is None:
            return None
        if after_id is not None and not await db.scalar(
            select(exists().where(Message.id == after_id, Message.call_record_id == record_id))
        ):
            return None
        messages = list(
            await db.scalars(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.conversation_id == conversation_id,
                    Message.call_record_id == record_id,
                    Message.id > (after_id if after_id is not None else record_id),
                    Message.role.in_(("user", "assistant")),
                    Message.tool_calls.is_(None),
                )
                .order_by(Message.id)
                .limit(limit + 1)
            )
        )
        utterances: list[CallUtterance] = []
        for message in messages[:limit]:
            if message.role == "user":
                utterances.append(CallUtterance(role="user", text=message_text(message)))
            elif message.reply_json:
                reply = CompanionReply.from_saved_snapshot(message.reply_json, message.content or "")
                utterances.extend(
                    CallUtterance(
                        role="assistant", text=bubble.title if isinstance(bubble, LinkBubble) else bubble.text
                    )
                    for bubble in reply.bubbles
                    if isinstance(bubble, (TextBubble, VoiceBubble, LinkBubble))
                )
        return CallRecordPage(
            duration_ms=row.call_duration_ms,
            started_at=row.created_at,
            ended_at=row.call_last_seen_at,
            utterances=utterances,
            next_cursor=messages[limit - 1].id if len(messages) > limit else None,
        )
