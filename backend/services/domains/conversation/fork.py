"""会话派生服务。`special` / `im` 语义上不可分叉，仅 `kind='standard'` 可派生——这是协议约束故抛业务异常，不走 HTTP 边界。"""

from modules.conversation import CompanionReply, Conversation, MediaBubble, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context_window import CHECKPOINT_SUBTYPES
from .history import build_session_messages
from .main_conversation import IM_KIND, SPECIAL_KIND, STANDARD_KIND, UI_ONLY_SUBTYPES
from .memory_scope import conversation_memory_scope


class ForkNotAllowedError(Exception):
    """源会话 kind 不可派生（special / im）。"""


class SourceNotFoundError(Exception):
    """源会话不存在 / 不属于该用户 / 源消息不在源会话内。"""


async def fork_conversation_from_message(
    db: AsyncSession,
    user_id: int,
    source_session_id: str,
    source_message_id: int,
) -> dict:
    """派生新会话：复制源会话中 ``id <= source_message_id`` 且不属于 ``UI_ONLY_SUBTYPES`` 的全部消息及历史元数据，复制行按已发送历史对待。返回精简版 ``SessionResumeResult``（不含 info）；runtime 挂载与 info 由 handler 补。"""
    src = await Conversation.by_session_id(db, source_session_id, user_id=user_id)
    if src is None:
        raise SourceNotFoundError(f"源会话不存在或不属于当前用户: {source_session_id!r}")

    conversation_memory_scope(src, user_id)

    if src.kind in (SPECIAL_KIND, IM_KIND):
        raise ForkNotAllowedError(f"该类型会话不可派生 (kind={src.kind!r})")

    src_msg = (
        await db.execute(
            select(Message.id, Message.subtype).where(
                Message.id == source_message_id,
                Message.conversation_id == src.id,
            ),
        )
    ).first()
    if src_msg is None:
        raise SourceNotFoundError(
            f"源消息不在会话内: message_id={source_message_id} session_id={source_session_id!r}",
        )

    # 只排除界面专用行；媒体送达、主动消息与摘要行属于模型上下文，照常复制。
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == src.id,
                    Message.id <= source_message_id,
                    Message.id > src.context_after_message_id,
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                )
                .order_by(Message.id),
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        # 源消息自身是界面专用行时防御性兜底，正常路径不会到这里
        raise SourceNotFoundError(
            f"无可派生的消息：会话 {source_session_id!r} 在 message_id={source_message_id} 之前没有可复制行",
        )

    # 继承 cwd / settings_json 让 runtime 启动即处于暖态；派生会话是独立的顶层会话，只记录来源。
    new_conv = Conversation(
        user_id=user_id,
        forked_from_id=src.id,
        kind=STANDARD_KIND,
        title=f"{(src.title or '新对话')} — 副本",
        cwd=src.cwd,
        settings_json=src.settings_json,
        system_preset_id=src.system_preset_id,
        is_automation=src.is_automation,
        is_deletable=True,
        is_renamable=True,
    )
    db.add(new_conv)
    await db.flush()

    # 统计列清零；tool_calls / media_json / content_type 原样复制以保证工具调用链自洽。
    copies: dict[int, Message] = {}
    for row in rows:
        copied_reply = row.reply_json
        if copied_reply:
            reply = CompanionReply.model_validate_json(copied_reply)
            for bubble in reply.bubbles:
                if isinstance(bubble, MediaBubble):
                    bubble.job_id = None
                    if bubble.status == "pending":
                        bubble.status, bubble.url, bubble.error = "failed", None, "生成任务属于原会话"
            copied_reply = reply.model_dump_json()
        copy = Message(
            conversation_id=new_conv.id,
            role=row.role,
            subtype=row.subtype,
            content=row.content,
            tool_calls=row.tool_calls,
            tool_call_id=row.tool_call_id,
            prompt_tokens=0,
            completion_tokens=0,
            turn_duration_ms=0,
            content_type=row.content_type,
            media_json=row.media_json,
            reasoning_content=row.reasoning_content,
            reply_json=copied_reply,
            summary_date=row.summary_date,
            created_at=row.created_at,
        )
        db.add(copy)
        copies[row.id] = copy
    await db.flush()
    for row in rows:
        if row.subtype in CHECKPOINT_SUBTYPES:
            boundary = copies.get(row.summary_through_message_id)
            if boundary is None:
                raise SourceNotFoundError("摘要覆盖边界不在可派生历史中")
            copies[row.id].summary_through_message_id = boundary.id

    await db.commit()

    messages = await build_session_messages(new_conv.id, db)

    return {
        "session_id": str(new_conv.id),
        "message_count": len(messages),
        "messages": messages,
    }
