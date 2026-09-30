from modules.channels import ChannelBinding
from modules.conversation import Conversation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import IM_KIND


async def _find_im_conversation_id(db: AsyncSession, binding: ChannelBinding) -> int | None:
    """按锚点找绑定专属 im 会话；锚点悬空（会话被删 SET NULL 前）或 kind 漂移时返回 None 走重建。"""
    if binding.conversation_id is None:
        return None
    return (
        await db.execute(
            select(Conversation.id).where(
                Conversation.id == binding.conversation_id,
                Conversation.user_id == binding.user_id,
                Conversation.kind == IM_KIND,
            ),
        )
    ).scalar_one_or_none()


async def get_or_create_channel_conversation_id(db: AsyncSession, binding: ChannelBinding, title: str) -> int:
    """获取/创建绑定专属 im 会话并返回其 id：conversation_id 唯一外键是「每渠道一条对话」的 DB 级锚点。并发读-插非原子——两条路径同时建会话时，败者 binding 回填撞 conversation_id UNIQUE、整个事务回滚，重读锚点收敛到胜者会话（沿 get_or_create_special_conversation 的 IntegrityError 收敛模式）。"""
    conv_id = await _find_im_conversation_id(db, binding)
    if conv_id is not None:
        return conv_id

    conv = Conversation(user_id=binding.user_id, kind=IM_KIND, title=title, system_preset_id="companion")
    db.add(conv)
    await db.flush()
    binding.conversation_id = conv.id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await db.refresh(binding)
        existing = await _find_im_conversation_id(db, binding)
        if existing is not None:
            return existing
        raise
    return conv.id
