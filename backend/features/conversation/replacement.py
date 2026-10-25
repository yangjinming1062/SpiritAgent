"""用户主动更换最新终端回答；原输入与已执行工具保持同一回合。"""

from dataclasses import dataclass
from typing import Literal, overload

from infrastructure.ai.selection import GenerationOrigin
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import ScalarSelect

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
            GenerationOrigin.model_validate(self.previous.generation_origin_json)
            if self.previous.generation_origin_json
            else None
        )


@overload
async def _replacement_candidates(
    db: AsyncSession,
    conv: Conversation | None,
    message_id: int,
    *,
    with_message: Literal[False],
) -> tuple[int, int]: ...


@overload
async def _replacement_candidates(
    db: AsyncSession,
    conv: Conversation | None,
    message_id: int,
    *,
    with_message: Literal[True],
) -> tuple[Message, Message]: ...


async def _replacement_candidates(
    db: AsyncSession,
    conv: Conversation | None,
    message_id: int,
    *,
    with_message: bool,
) -> tuple[int, int] | tuple[Message, Message]:
    """校验换回答资格并返回 (用户源行, 最新回答)；with_message 时同查询附带实体，否则仅取轻投影 id。"""
    if conv is None or conv.is_automation or conv.parent_id is not None or conv.kind not in {"standard", "special"}:
        raise ReplyReplacementNotAllowedError("当前会话不支持换一个回答")
    latest_stmt = select(
        Message.id,
        Message.role,
        Message.subtype,
        Message.tool_calls.is_(None).label("terminal"),
        or_(Message.content != "", Message.media_json != [], Message.reply_json != {}).label("has_content"),
    )
    source_stmt = select(Message.id, Message.subtype)
    if with_message:
        latest_stmt = latest_stmt.add_columns(Message)
        source_stmt = source_stmt.add_columns(Message)
    latest = (
        await db.execute(
            latest_stmt.where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).one_or_none()
    if (
        latest is None
        or latest.id != message_id
        or latest.role != "assistant"
        or latest.subtype is not None
        or not latest.terminal
        or not latest.has_content
    ):
        raise ReplyReplacementNotAllowedError("只能更换最新一轮的完整回答，请刷新后重试")
    source = (
        await db.execute(
            source_stmt.where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).one_or_none()
    if source is None or source.subtype is not None or source.id >= latest.id:
        raise ReplyReplacementNotAllowedError("没有可复用的用户消息")
    if source.id <= await context_message_watermark(db, conv):
        raise ReplyReplacementNotAllowedError("该回合已不在当前上下文中，无法更换回答")
    return (source.Message, latest.Message) if with_message else (source.id, latest.id)


async def replacement_source_id(db: AsyncSession, user_id: int, session_id: str, message_id: int) -> int:
    """校验换回答资格并返回可复用的用户消息 id；缺行或不合格仍抛换回答禁用错误。"""
    conv = await Conversation.by_session_id(db, session_id, user_id)
    return (await _replacement_candidates(db, conv, message_id, with_message=False))[0]


async def reply_replacement_target(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    message_id: int,
) -> ReplyReplacement:
    """调用方持有会话锁；摘要覆盖或新消息出现后，旧回答不再允许更换。"""
    conv = await Conversation.by_session_id(db, session_id, user_id)
    source, target = await _replacement_candidates(db, conv, message_id, with_message=True)
    return ReplyReplacement(source.id, message_text(source), message_snapshot(target))


def replaceable_reply_expression(conv_id: int) -> ScalarSelect[int]:
    """列表读取在原查询中附带替换资格，写操作仍重新核对完整原始回合。"""
    latest = (
        select(
            Message.id,
            Message.role,
            Message.subtype,
            Message.tool_calls,
            Message.content,
            Message.media_json,
            Message.reply_json,
        )
        .where(Message.conversation_id == conv_id, Message.role.in_(("user", "assistant")))
        .order_by(Message.id.desc())
        .limit(1)
        .subquery()
    )
    source = (
        select(Message.id, Message.subtype)
        .where(Message.conversation_id == conv_id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(1)
        .subquery()
    )
    checkpoint = (
        select(Message.summary_through_message_id)
        .where(
            Message.conversation_id == conv_id,
            Message.id > Conversation.context_after_message_id,
            Message.subtype == "compress_summary",
        )
        .order_by(Message.id.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    return (
        select(latest.c.id)
        .select_from(Conversation)
        .join(latest, true())
        .join(source, true())
        .where(
            Conversation.id == conv_id,
            Conversation.is_automation.is_(False),
            Conversation.parent_id.is_(None),
            Conversation.kind.in_(("standard", "special")),
            latest.c.role == "assistant",
            latest.c.subtype.is_(None),
            latest.c.tool_calls.is_(None),
            or_(latest.c.content != "", latest.c.media_json != [], latest.c.reply_json != {}),
            source.c.subtype.is_(None),
            source.c.id < latest.c.id,
            source.c.id > func.greatest(Conversation.context_after_message_id, func.coalesce(checkpoint, 0)),
        )
        .scalar_subquery()
    )
