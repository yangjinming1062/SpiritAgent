"""伙伴资料公开请求，返回用户可见 DTO。"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from features.life.queries import get_life_mood

from .models import Persona
from .persona_background import schedule_personality_tag_refresh
from .persona_service import load_persona_definition, update_persona
from .schemas import PersonaResponse


def _response(persona: Persona | None, current_mood: str | None) -> PersonaResponse:
    tags = persona.personality_tags_json if persona else []
    return PersonaResponse(
        is_complete=persona.is_complete if persona else False,
        definition_json=load_persona_definition(persona),
        personality_tags=tags if isinstance(tags, list) else [],
        current_mood=current_mood,
    )


async def get_persona_view(db: AsyncSession, user_id: int) -> PersonaResponse:
    persona = await db.get(Persona, user_id)
    mood = await get_life_mood(db, user_id)
    return _response(persona, mood)


async def update_persona_view(
    db: AsyncSession,
    user_id: int,
    definition: dict[str, Any],
    *,
    expected_fields: Mapping[str, str | None] | None = None,
) -> PersonaResponse:
    persona = await update_persona(db, user_id, definition, expected_fields=expected_fields)
    mood = await get_life_mood(db, user_id)
    schedule_personality_tag_refresh(user_id)
    return _response(persona, mood)
