"""账户内会话标识查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Conversation


async def conversation_ids(db: AsyncSession, user_id: int) -> tuple[int, ...]:
    return tuple(await db.scalars(select(Conversation.id).where(Conversation.user_id == user_id)))
