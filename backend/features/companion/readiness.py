"""伙伴初始化状态的公开读模型。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Persona


async def persona_is_complete(db: AsyncSession, user_id: int) -> bool:
    return bool(await db.scalar(select(Persona.is_complete).where(Persona.user_id == user_id)))
