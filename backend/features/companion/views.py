"""伙伴资料公开请求，返回用户可见 DTO。"""

import json
from collections.abc import Mapping
from typing import Any

from infrastructure.text import safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from features.life.queries import get_life_mood

from .persona_background import schedule_personality_tag_refresh
from .persona_service import get_or_create_persona, load_persona_definition, update_persona
from .schemas import PersonaResponse


def _response(is_complete: bool, definition_json: str, tags_json: str, current_mood: str | None) -> PersonaResponse:
    tags = safe_json_loads(tags_json or "[]", default=[])
    return PersonaResponse(
        is_complete=is_complete,
        definition_json=definition_json,
        personality_tags=tags if isinstance(tags, list) else [],
        current_mood=current_mood,
    )


async def get_persona_view(db: AsyncSession, user_id: int) -> PersonaResponse:
    persona = await get_or_create_persona(db, user_id)
    mood = await get_life_mood(db, user_id)
    return _response(
        persona.is_complete,
        json.dumps(load_persona_definition(persona), ensure_ascii=False),
        persona.personality_tags_json,
        mood,
    )


async def update_persona_view(
    db: AsyncSession,
    user_id: int,
    definition: dict[str, Any],
    *,
    expected_fields: Mapping[str, str | None] | None = None,
) -> PersonaResponse:
    persona = await update_persona(db, user_id, definition, expected_fields=expected_fields)
    mood = await get_life_mood(db, user_id)
    schedule_personality_tag_refresh(persona.id, user_id)
    return _response(persona.is_complete, persona.definition_json, persona.personality_tags_json, mood)
