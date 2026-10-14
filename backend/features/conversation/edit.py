"""用户消息的文本替换；附件保留，源消息及其后的历史在同一事务中替换。"""

import json
import re

from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.text import safe_json_loads
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.asset_releases import collect_message_asset_releases
from features.conversation.formatting import message_text
from features.conversation.last_user_message import find_user_message
from features.conversation.models import Message
from features.conversation.reply_audio import cancel_reply_audio

_ATTACHMENT_DIRECTIVE = re.compile(r"^@(file|folder):", re.IGNORECASE)


class EditNotAllowedError(ValueError):
    """消息已经过期、不可编辑或输入无效；失败不改变历史。"""


async def replace_user_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
    text: str,
) -> Message:
    """调用方持有会话锁且确认没有在途回合；本函数提交替换并返回新用户行。"""
    if not text.strip():
        raise EditNotAllowedError("编辑后的消息不能为空")
    conv, source = await find_user_message(db, user_id, session_id, source_message_id)
    if conv is None:
        raise EditNotAllowedError("会话不存在或不支持编辑消息")
    if source is None:
        raise EditNotAllowedError("消息不存在、不可编辑或已过期，请刷新后重试")

    # 文件 / 文件夹引用仍存于正文末尾；编辑器只修改可见文本，不丢失原引用。
    directives = [line for line in message_text(source).splitlines() if _ATTACHMENT_DIRECTIVE.match(line.strip())]
    content = "\n".join([text.strip(), *directives])
    if source.content_type == "multimodal_v1":
        parts = safe_json_loads(source.content or "", default=None)
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise EditNotAllowedError("原消息附件格式无效，无法编辑")
        content = json.dumps(
            [
                {"type": "input_text", "text": content},
                *(part for part in parts if part.get("type") != "input_text"),
            ],
            ensure_ascii=False,
        )

    replacement = Message(
        conversation_id=conv.id,
        role="user",
        content=content,
        content_type=source.content_type,
    )
    async with user_asset_lock(user_id):
        removed = await collect_message_asset_releases(
            db,
            user_id,
            [conv.id],
            from_message_id=source_message_id,
        )
        await db.execute(
            delete(Message).where(Message.conversation_id == conv.id, Message.id >= source_message_id),
        )
        db.add(replacement)
        # 新 id 使旧快照的 after_id 失效，断线重连必须全量恢复修订后的历史。
        await db.commit()
    await cancel_reply_audio(user_id, removed)
    return replacement
