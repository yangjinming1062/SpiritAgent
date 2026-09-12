from modules.channels import ChannelBinding
from modules.conversation import Conversation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import IM_KIND


async def _find_im_conversation(db: AsyncSession, binding: ChannelBinding) -> Conversation | None:
    """按锚点找绑定专属 im 会话；锚点悬空（会话被删 SET NULL 前）或 kind 漂移时返回 None 走重建。"""
    if binding.conversation_id is None:
        return None
    return (
        await db.execute(
            select(Conversation).where(
                Conversation.id == binding.conversation_id,
                Conversation.user_id == binding.user_id,
                Conversation.kind == IM_KIND,
            ),
        )
    ).scalar_one_or_none()


async def get_or_create_channel_conversation(
    db: AsyncSession,
    binding: ChannelBinding,
    title_override: str | None = None,
) -> Conversation:
    """获取/创建绑定专属 im 会话：conversation_id 唯一外键是「每渠道一条对话」的 DB 级锚点。

    并发调用方读-插非原子：两条路径同时为同一绑定建会话时，败者的 binding 回填撞 conversation_id
    UNIQUE、整个事务（含会话 INSERT）回滚，重读锚点收敛到胜者会话（沿 get_or_create_special_conversation
    的 IntegrityError 收敛模式，唯一性锚点从 (user_id, system_preset_id) 部分索引换成 binding 列）。
    """
    conv = await _find_im_conversation(db, binding)
    if conv is not None:
        return conv

    title = (title_override or "").strip() or "IM 对话"
    conv = Conversation(user_id=binding.user_id, kind=IM_KIND, title=title)
    db.add(conv)
    await db.flush()
    binding.conversation_id = conv.id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await db.refresh(binding)
        existing = await _find_im_conversation(db, binding)
        if existing is not None:
            return existing
        raise
    await db.refresh(conv)
    return conv
