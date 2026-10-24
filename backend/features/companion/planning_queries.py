"""夜间消费的伙伴资料与策略快照。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.life.queries import get_life_mood

from .models import Persona
from .persona_service import load_persona_definition


@dataclass(frozen=True)
class PersonaPlanningSnapshot:
    is_complete: bool
    current_mood: str | None
    outfit_policy: str
    scene_policy: str
    definition: dict[str, str]


async def planning_persona(db: AsyncSession, user_id: int) -> PersonaPlanningSnapshot | None:
    row = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    mood = await get_life_mood(db, user_id)
    return (
        PersonaPlanningSnapshot(
            row.is_complete,
            mood,
            row.outfit_policy,
            row.scene_policy,
            load_persona_definition(row),
        )
        if row is not None
        else None
    )
