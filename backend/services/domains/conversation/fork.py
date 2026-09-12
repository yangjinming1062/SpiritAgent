"""会话派生服务。`special` / `im` 语义上不可分叉，仅 `kind='standard'` 可派生——这是协议约束故抛业务异常，不走 HTTP 边界。"""

from modules.conversation import Conversation, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .history import build_session_messages
from .main_conversation import IM_KIND, SPECIAL_KIND, STANDARD_KIND


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
    """派生新会话：复制源会话中 ``id <= source_message_id`` 且非 ``status_*`` 的全部消息及历史元数据，复制行按已发送历史对待。返回精简版 ``SessionResumeResult``（不含 info）；runtime 挂载与 info 由 handler 补。"""
    src = await Conversation.by_session_id(db, source_session_id, user_id=user_id)
    if src is None:
        raise SourceNotFoundError(f"源会话不存在或不属于当前用户: {source_session_id!r}")

    if src.kind in (SPECIAL_KIND, IM_KIND):
        raise ForkNotAllowedError(f"该类型会话不可派生 (kind={src.kind!r})")

    # 源消息必须属于该会话
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

    # 排除 status_* 行（UI 痕迹，不入 LLM 上下文）；保留 compress_summary / daily_summary
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == src.id,
                    Message.id <= source_message_id,
                    Message.subtype.is_(None) | ~Message.subtype.like("status_%"),
                )
                .order_by(Message.id),
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        # 源消息自身就是 status_* 时防御性兜底，正常路径不会到这里
        raise SourceNotFoundError(
            f"无可派生的消息：会话 {source_session_id!r} 在 message_id={source_message_id} 之前没有可复制行",
        )

    # 继承 cwd / settings_json 让 runtime 启动即处于暖态；parent_id 串血缘供 REST lineage_root_id 暴露
    new_conv = Conversation(
        user_id=user_id,
        parent_id=src.id,
        kind=STANDARD_KIND,
        title=f"{(src.title or '新对话')} — 副本",
        cwd=src.cwd,
        settings_json=src.settings_json,
        system_preset_id=src.system_preset_id,
        is_deletable=True,
        is_renamable=True,
    )
    db.add(new_conv)
    await db.flush()

    # 统计列清零；tool_calls / media_json / content_type 原样复制以保证工具调用链自洽。
    for row in rows:
        db.add(
            Message(
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
                speech_style_json=row.speech_style_json,
                summary_date=row.summary_date,
                created_at=row.created_at,
            ),
        )

    await db.commit()
    await db.refresh(new_conv)

    messages = await build_session_messages(new_conv.id, db, include_id=True)

    return {
        "session_id": str(new_conv.id),
        "message_count": len(messages),
        "messages": messages,
    }
