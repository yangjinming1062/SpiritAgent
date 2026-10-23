"""影像策划的声音和相关记忆上下文，只供当次调用，不复制进成品快照。"""

from features.action_library.queries import action_detail
from features.appearance.queries import outfit_snapshot
from features.companion.visual_state import persona_snapshot
from features.configuration.settings.timezone import resolve_user_timezone
from features.media.voice_queries import resolve_custom_voice
from features.media.voices import read_current_voice
from features.memory.memory_retrieval import retrieve_hybrid_memories
from features.scenes.queries import scene_snapshot
from features.visuals.ports import VisualMaterialLinks, VisualPlanningContext
from features.visuals.schemas import ComboSnapshot
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import format_local_iso, utc_now
from runtime.memory_scope import MemoryScope


async def visual_planning_context(user_id: int, snapshot: ComboSnapshot) -> VisualPlanningContext:
    async with SESSION_LOCAL() as db:
        persona = await persona_snapshot(db, user_id)
        voice_id = await read_current_voice(db, user_id)
        voice = await resolve_custom_voice(db, user_id, voice_id) if voice_id else None
        memories = await retrieve_hybrid_memories(
            db,
            MemoryScope(user_id, "companion"),
            " ".join((snapshot.action.name, snapshot.action.motion_description, snapshot.requirements)),
            limit=6,
        )
        now = format_local_iso(utc_now(), await resolve_user_timezone(db, user_id)) or ""
    return VisualPlanningContext(
        voice_style=voice.prompt if voice is not None else "",
        current_time=now,
        current_mood=(persona.current_mood or "") if persona else "",
        relevant_memories=[{"content": item.content, "basis": item.basis} for item in memories],
    )


async def visual_material_links(user_id: int, snapshot: ComboSnapshot) -> VisualMaterialLinks:
    async with SESSION_LOCAL() as db:
        outfit = (
            await outfit_snapshot(db, user_id, snapshot.outfit.material_id) if snapshot.outfit.material_id else None
        )
        scene = await scene_snapshot(db, user_id, snapshot.scene.material_id) if snapshot.scene.material_id else None
        action = await action_detail(db, user_id, snapshot.action.action_id) if snapshot.action.action_id else None
    return VisualMaterialLinks(
        outfit_id=outfit.id if outfit else None,
        scene_id=scene.id if scene else None,
        action_id=action.id if action else None,
    )
