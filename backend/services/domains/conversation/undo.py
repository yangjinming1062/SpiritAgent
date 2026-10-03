"""会话撤回：硬删除 ``id >= source_message_id`` 的全部行（含锚点本身），把锚点的用户正文与图片附件推回客户端作为输入框草稿。与 ``fork`` 互为对偶——fork 在新会话复制 1..N，本服务在原会话硬删 N..end。调用方必须先 ``resolve_undo_target`` 再 prune 视频，最后才调用 ``undo_conversation_to_message``。"""

from components import safe_json_loads
from modules.conversation import Conversation, Message, UndoAnchor, UndoAttachment, UndoResult
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

    if conv.is_automation or conv.kind in (SPECIAL_KIND, IM_KIND):
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


def _undo_anchor(content: str | None, content_type: str) -> UndoAnchor:
    """多模态行的用户正文固定在 parts[0]，其后的 input_text 是视频清理或降级占位；只有 data URL 图片能经提交流程重新附加，视频随撤回清理、不恢复。"""
    text = content or ""
    parts = safe_json_loads(text, default=None) if content_type == "multimodal_v1" else None
    if not isinstance(parts, list):
        return UndoAnchor(text=text, attachments=[])
    attachments = [
        UndoAttachment(type="image", url=part["image_url"])
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "input_image"
        and isinstance(part.get("image_url"), str)
        and part["image_url"].startswith("data:image/")
    ]
    first = parts[0] if parts else None
    body = first.get("text") if isinstance(first, dict) and first.get("type") == "input_text" else None
    return UndoAnchor(text=str(body or ""), attachments=attachments)


async def undo_conversation_to_message(
    db: AsyncSession,
    conv: Conversation,
    source_message_id: int,
) -> UndoResult:
    """硬删 ``id >= source_message_id`` 的行并 hydrate；调用方必须已 resolve 且已 prune。"""
    anchor_row = (
        await db.execute(
            select(Message.content, Message.content_type).where(
                Message.id == source_message_id,
                Message.conversation_id == conv.id,
            ),
        )
    ).first()
    if anchor_row is None:
        raise SourceNotFoundError(
            f"锚点消息不在会话内: message_id={source_message_id} conversation_id={conv.id}",
        )
    anchor_content, anchor_content_type = anchor_row

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

    delivered = await build_session_messages(conv.id, db)

    return UndoResult(
        session_id=str(conv.id),
        deleted_count=int(deleted_count),
        anchor=_undo_anchor(anchor_content, anchor_content_type),
        messages=delivered,
    )
