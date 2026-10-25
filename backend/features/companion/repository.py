"""伙伴资料内部持久入口，调用方事务负责提交。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Persona


async def get_or_create_persona_row(db: AsyncSession, user_id: int) -> Persona:
    row = await db.scalar(select(Persona).where(Persona.user_id == user_id))
    if row is None:
        await db.execute(
            insert(Persona)
            .values(user_id=user_id, definition_json={})
            .on_conflict_do_nothing(index_elements=[Persona.user_id]),
        )
        row = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one()
    return row
