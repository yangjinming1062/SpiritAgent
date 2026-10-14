"""视觉业务读取伙伴资料及更新政策、确认和场景版本的事务入口。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Persona
from .query_contracts import PersonaSnapshot
from .repository import get_or_create_persona_row


async def persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot | None:
    row = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    return PersonaSnapshot.model_validate(row) if row is not None else None


async def ensure_persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    return PersonaSnapshot.model_validate(await get_or_create_persona_row(db, user_id))


async def lock_persona_snapshot(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    row = await db.scalar(
        select(Persona).where(Persona.user_id == user_id).with_for_update().execution_options(populate_existing=True),
    )
    if row is None:
        raise ValueError("Persona is no longer available")
    return PersonaSnapshot.model_validate(row)


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


async def change_scene_policy(db: AsyncSession, user_id: int, policy: str) -> PersonaSnapshot:
    row = await _persona_row(db, user_id)
    row.scene_policy = policy
    return PersonaSnapshot.model_validate(row)


async def advance_scene_switch(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    row = await _persona_row(db, user_id)
    row.scene_switch_version += 1
    return PersonaSnapshot.model_validate(row)


async def advance_scene_state(db: AsyncSession, user_id: int) -> PersonaSnapshot:
    row = await _persona_row(db, user_id)
    row.scene_state_version += 1
    return PersonaSnapshot.model_validate(row)


async def set_active_scene(db: AsyncSession, user_id: int, scene_id: int) -> PersonaSnapshot:
    row = await _persona_row(db, user_id)
    row.active_scene_id = scene_id
    return PersonaSnapshot.model_validate(row)
