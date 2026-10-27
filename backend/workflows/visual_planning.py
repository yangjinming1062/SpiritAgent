"""影像策划的声音和相关记忆上下文，只供当次调用，不复制进成品快照。"""

from features.configuration.settings.timezone import resolve_user_timezone
from features.life.context import read_life_context_in_session
from features.media.voice_queries import resolve_custom_voice
from features.media.voices import read_current_voice
from features.memory.memory_retrieval import retrieve_hybrid_memories
from features.visuals.ports import VisualPlanningContext
from features.visuals.schemas import ActorClipSource, CinematicSource
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import format_local_iso, utc_now
from runtime.memory_scope import MemoryScope


def _memory_query(snapshot: ActorClipSource | CinematicSource) -> str:
    action = snapshot.action
    return " ".join((action.name, action.motion_description, snapshot.requirements))


async def visual_planning_context(user_id: int, snapshot: ActorClipSource | CinematicSource) -> VisualPlanningContext:
    async with SESSION_LOCAL() as db:
        life_context = await read_life_context_in_session(db, user_id)
        voice_id = await read_current_voice(db, user_id)
        voice = await resolve_custom_voice(db, user_id, voice_id) if voice_id else None
        memories = await retrieve_hybrid_memories(
            db,
            MemoryScope(user_id, "companion"),
            _memory_query(snapshot),
            limit=6,
        )
        now = format_local_iso(utc_now(), await resolve_user_timezone(db, user_id)) or ""
    return VisualPlanningContext(
        voice_style=voice.prompt if voice is not None else "",
        current_time=now,
        life_context=life_context.prompt_payload(),
        relevant_memories=[{"content": item.content, "basis": item.basis} for item in memories],
    )
