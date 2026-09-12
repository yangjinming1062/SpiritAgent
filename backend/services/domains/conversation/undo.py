"""会话撤回：硬删除 ``id >= source_message_id`` 的全部行（含锚点本身），把锚点载荷推回客户端作为输入框草稿。与 ``fork`` 互为对偶——fork 在新会话复制 1..N，本服务在原会话硬删 N..end。

调用方必须先 ``resolve_undo_target`` 再 prune 视频，最后才调用 ``undo_conversation_to_message``。
"""

from modules.conversation import Conversation, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .fork import SourceNotFoundError
from .history import build_session_messages
from .main_conversation import IM_KIND, SPECIAL_KIND


class UndoNotAllowedError(Exception):
    """会话 kind 不可撤回（special / im）。"""


async def resolve_undo_target(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> Conversation:
    """校验会话归属、kind、锚点存在且为 user-role；通过后返回 Conversation。不碰磁盘、不删行。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise SourceNotFoundError(f"会话不存在或不属于当前用户: {session_id!r}")

    if conv.kind in (SPECIAL_KIND, IM_KIND):
        raise UndoNotAllowedError(f"该类型会话不可撤回 (kind={conv.kind!r})")

    anchor_row = (
        await db.execute(
            select(Message.role).where(
                Message.id == source_message_id,
                Message.conversation_id == conv.id,
            ),
        )
    ).first()
    if anchor_row is None:
        raise SourceNotFoundError(
            f"锚点消息不在会话内: message_id={source_message_id} session_id={session_id!r}",
        )

    (anchor_role,) = anchor_row
    if anchor_role != "user":
        raise UndoNotAllowedError(
            f"撤回仅支持 user-role 消息（source_message_id={source_message_id} 是 {anchor_role!r}）",
        )
    return conv


async def undo_conversation_to_message(
    db: AsyncSession,
    conv: Conversation,
    source_message_id: int,
) -> dict:
    """硬删 ``id >= source_message_id`` 的行并 hydrate；调用方必须已 resolve 且已 prune。"""
    anchor_row = (
        await db.execute(
            select(Message.content, Message.content_type, Message.media_json).where(
                Message.id == source_message_id,
                Message.conversation_id == conv.id,
            ),
        )
    ).first()
    if anchor_row is None:
        raise SourceNotFoundError(
            f"锚点消息不在会话内: message_id={source_message_id} conversation_id={conv.id}",
        )
    anchor_content, anchor_content_type, anchor_media_json = anchor_row

    deleted_count = (
        await db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv.id,
                Message.id >= source_message_id,
            ),
        )
    ).scalar_one()

    await db.execute(
        delete(Message).where(
            Message.conversation_id == conv.id,
            Message.id >= source_message_id,
        ),
    )
    await db.commit()

    delivered = await build_session_messages(conv.id, db, include_id=True)

    return {
        "session_id": str(conv.id),
        "deleted_count": int(deleted_count),
        "anchor": {
            "text": anchor_content or "",
            "content_type": anchor_content_type or "text",
            "media_json": anchor_media_json,
        },
        "messages": delivered,
    }
