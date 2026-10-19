"""账户初始化与恢复共用的陪伴主会话入口。"""

from sqlalchemy.ext.asyncio import AsyncSession

from features.conversation.main_conversation import get_or_create_main_conversation


async def ensure_main_conversation_for_user(db: AsyncSession, user_id: int, *, commit: bool = True) -> str:
    """幂等确保唯一系统对话存在，返回可跨模块使用的会话 ID。"""
    conversation = await get_or_create_main_conversation(db, user_id, commit=commit)
    return str(conversation.id)
