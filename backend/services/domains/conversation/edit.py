"""最后一条用户消息的文本替换；附件保留，旧回合与新输入在同一事务中替换。"""

import json
import re

from components import safe_json_loads
from modules.conversation import Conversation, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .formatting import message_text
from .main_conversation import SPECIAL_KIND, STANDARD_KIND

_ATTACHMENT_DIRECTIVE = re.compile(r"^@(file|folder):", re.IGNORECASE)


class EditNotAllowedError(ValueError):
    """消息已经过期、不可编辑或输入无效；失败不改变历史。"""


async def replace_last_user_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
    text: str,
) -> Message:
    """调用方持有会话锁且确认没有在途回合；本函数提交替换并返回新用户行。"""
    if not text.strip():
        raise EditNotAllowedError("编辑后的消息不能为空")
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None or conv.kind not in (STANDARD_KIND, SPECIAL_KIND):
        raise EditNotAllowedError("会话不存在或不支持编辑消息")

    source = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    if (
        source is None
        or source.id != source_message_id
        or source.subtype
        or source.queued
        or source.id <= conv.context_after_message_id
    ):
        raise EditNotAllowedError("只能编辑当前对话最后一条用户消息，请刷新后重试")

    # 文件 / 文件夹引用仍存于正文末尾；编辑器只修改可见文本，不丢失原引用。
    directives = [line for line in message_text(source).splitlines() if _ATTACHMENT_DIRECTIVE.match(line.strip())]
    content = "\n".join([text.strip(), *directives])
    if source.content_type == "multimodal_v1":
        parts = safe_json_loads(source.content, default=None)
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise EditNotAllowedError("原消息附件格式无效，无法编辑")
        content = json.dumps(
            [
                {"type": "input_text", "text": content},
                *(part for part in parts if part.get("type") not in ("input_text", "text")),
            ],
            ensure_ascii=False,
        )

    replacement = Message(
        conversation_id=conv.id,
        role="user",
        content=content,
        content_type=source.content_type,
    )
    await db.execute(
        delete(Message).where(Message.conversation_id == conv.id, Message.id >= source_message_id),
    )
    db.add(replacement)
    # 新 id 使旧快照的 after_id 失效，断线重连必须全量恢复修订后的历史。
    await db.commit()
    return replacement
