"""视觉业务读取伙伴资料及更新政策、确认的事务入口。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from features.life.queries import get_life_mood

from .models import Persona
from .query_contracts import PersonaSnapshot
from .repository import get_or_create_persona_row


async def _snapshot(db: AsyncSession, row: Persona) -> PersonaSnapshot:
    mood = await get_life_mood(db, row.user_id)
    return PersonaSnapshot.model_validate(
        {
            **{key: getattr(row, key) for key in PersonaSnapshot.model_fields if key != "current_mood"},
            "current_mood": mood,
        }
    )


async def persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot | None:
    row = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    return await _snapshot(db, row) if row is not None else None


async def ensure_persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    return await _snapshot(db, await get_or_create_persona_row(db, user_id))


async def lock_persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    row = await db.scalar(
        select(Persona).where(Persona.user_id == user_id).with_for_update().execution_options(populate_existing=True),
    )
    if row is None:
        raise ValueError("Persona is no longer available")
    return await _snapshot(db, row)


async def _persona_row(db: AsyncSession, user_id: int) -> Persona:
    row = await db.scalar(select(Persona).where(Persona.user_id == user_id).with_for_update())
    if row is None:
        raise ValueError("Persona is no longer available")
    return row


async def reset_portrait_confirmation(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(Persona)
        .where(Persona.user_id == user_id)
        .values(is_portrait_confirmed=False, portrait_confirmed_at=None),
    )


async def change_outfit_policy(db: AsyncSession, user_id: int, policy: str) -> None:
    row = await get_or_create_persona_row(db, user_id)
    row.outfit_policy = policy


async def read_scene_policy(db: AsyncSession, user_id: int) -> str | None:
    """环境读取只需要添置政策，不加载人设正文或生活状态。"""
    return await db.scalar(select(Persona.scene_policy).where(Persona.user_id == user_id))


async def change_scene_policy(db: AsyncSession, user_id: int, policy: str) -> PersonaSnapshot:
    row = await _persona_row(db, user_id)
    row.scene_policy = policy
    return await _snapshot(db, row)
