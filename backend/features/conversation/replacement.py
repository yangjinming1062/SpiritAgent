"""用户主动更换最新终端回答；原输入与已执行工具保持同一回合。"""

from dataclasses import dataclass

from infrastructure.ai.selection import GenerationOrigin
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.context_window import context_message_watermark
from features.conversation.formatting import message_text
from features.conversation.models import Conversation, Message
from features.conversation.snapshots import MessageSnapshot
from features.conversation.turn_access import message_snapshot


class ReplyReplacementNotAllowedError(ValueError):
    pass


@dataclass(frozen=True)
class ReplyReplacement:
    source_message_id: int
    text: str
    previous: MessageSnapshot

    @property
    def generation_origin(self) -> GenerationOrigin | None:
        return (
            GenerationOrigin.model_validate_json(self.previous.generation_origin_json)
            if self.previous.generation_origin_json
            else None
        )


async def _replacement_message_ids(
    db: AsyncSession,
    conv: Conversation | None,
    message_id: int | None = None,
) -> tuple[int, int]:
    if conv is None or conv.is_automation or conv.parent_id is not None or conv.kind not in {"standard", "special"}:
        raise ReplyReplacementNotAllowedError("当前会话不支持换一个回答")
    latest = (
        await db.execute(
            select(
                Message.id,
                Message.role,
                Message.subtype,
                Message.tool_calls.is_(None).label("terminal"),
                or_(Message.content != "", Message.media_json != "", Message.reply_json != "").label("has_content"),
            )
            .where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).one_or_none()
    if (
        latest is None
        or (message_id is not None and latest.id != message_id)
        or latest.role != "assistant"
        or latest.subtype is not None
        or not latest.terminal
        or not latest.has_content
    ):
        raise ReplyReplacementNotAllowedError("只能更换最新一轮的完整回答，请刷新后重试")
    source = (
        await db.execute(
            select(Message.id, Message.subtype)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).one_or_none()
    if source is None or source.subtype is not None or source.id >= latest.id:
        raise ReplyReplacementNotAllowedError("没有可复用的用户消息")
    if source.id <= await context_message_watermark(db, conv):
        raise ReplyReplacementNotAllowedError("该回合已不在当前上下文中，无法更换回答")
    return source.id, latest.id


async def reply_replacement_target(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    message_id: int,
) -> ReplyReplacement:
    """调用方持有会话锁；摘要覆盖或新消息出现后，旧回答不再允许更换。"""
    conv = await Conversation.by_session_id(db, session_id, user_id)
    source_id, target_id = await _replacement_message_ids(db, conv, message_id)
    source, target = await db.get(Message, source_id), await db.get(Message, target_id)
    if source is None or target is None:
        raise ReplyReplacementNotAllowedError("原回合已不可用，请刷新后重试")
    return ReplyReplacement(source.id, message_text(source), message_snapshot(target))


async def replaceable_reply_id(db: AsyncSession, conv_id: int) -> int | None:
    try:
        return (await _replacement_message_ids(db, await db.get(Conversation, conv_id)))[1]
    except ReplyReplacementNotAllowedError:
        return None
