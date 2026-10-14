"""伙伴资料的公开只读状态。"""

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Persona


async def portrait_is_confirmed(db: AsyncSession, user_id: int) -> bool | None:
    return await db.scalar(select(Persona.is_portrait_confirmed).where(Persona.user_id == user_id))


async def complete_user_ids(db: AsyncSession, user_ids: Collection[int]) -> set[int]:
    return set(
        await db.scalars(select(Persona.user_id).where(Persona.user_id.in_(user_ids), Persona.is_complete.is_(True))),
    )
