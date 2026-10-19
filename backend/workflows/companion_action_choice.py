"""日常自主表达只判断当前可用动作，实际播放由统一动作视图路由。"""

from typing import Literal

from features.companion.disturbance import get_disturbance_tier
from features.companion.proactive_runtime import get_presentation_snapshot, get_user_proactive_record
from features.companion.prompt_runtime import load_companion_prompt_context, run_prompt_json
from features.conversation.context_window import load_recent_context_window
from features.desktop_life.queries import activated_desktop_environment
from features.scenes.queries import get_scene_state, scene_environment
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text

from workflows.companion_action_prompts import ACTION_IDLE_EXPRESSION
from workflows.companion_actions import build_action_view, play_action_reference


async def choose_idle_action(
    user_id: int,
    idle_seconds: float,
    local_hour: int,
    *,
    trigger: Literal["periodic", "user_return"] = "periodic",
) -> dict[str, object]:
    presentation = get_presentation_snapshot(user_id)
    record = get_user_proactive_record(user_id)
    contact_revision = record.contact_revision
    if presentation is None or not record.available or record.busy_count:
        return {"expressed": False, "action_id": None, "reason": "unavailable"}
    async with SESSION_LOCAL() as db:
        view = await build_action_view(db, user_id, presentation=presentation, trigger=trigger, lock=True)
        recent = await load_recent_context_window(db, user_id)
        environment = (
            {"current": (await activated_desktop_environment(db, user_id)).get("current")}
            if presentation.mode == "desktop"
            else scene_environment(await get_scene_state(db, user_id))
        )
    if not view.items:
        return {"expressed": False, "action_id": None, "reason": "not_ready"}
    context = await load_companion_prompt_context(user_id)
    if context is None:
        return {"expressed": False, "action_id": None, "reason": "persona_not_ready"}
    config = await load_user_llm_config(user_id, AiTaskKey.proactive)
    choice, failure = await run_prompt_json(
        user_id,
        config,
        resolve_prompt_text(ACTION_IDLE_EXPRESSION, context.language),
        {
            "persona": context.persona_extras,
            "output_language": context.language,
            "current_mood": context.current_mood,
            "current_time": context.current_time,
            "long_term_memories": context.memories_block,
            "recent_context": recent,
            "environment": environment,
            "idle_seconds": idle_seconds,
            "trigger": trigger,
            "local_hour": local_hour if local_hour >= 0 else None,
            "current_action_ref": view.current_action_ref,
            "available_actions": [item.to_dict() for item in view.items],
        },
        log_prefix="idle_expression",
    )
    if choice is None:
        return {"expressed": False, "action_id": None, "reason": failure or "unparseable"}
    reference = choice.get("action_ref")
    selected = next((item for item in view.items if item.action_ref == reference), None)
    if reference is None:
        return {"expressed": False, "action_id": None, "reason": "keep_current"}
    if (
        not isinstance(reference, str)
        or selected is None
        or presentation != get_presentation_snapshot(user_id)
        or contact_revision != get_user_proactive_record(user_id).contact_revision
        or not get_user_proactive_record(user_id).available
        or get_user_proactive_record(user_id).busy_count
        or await get_disturbance_tier(user_id) != "autonomous"
    ):
        return {"expressed": False, "action_id": None, "reason": "state_changed"}
    reason = choice.get("reason")
    result = await play_action_reference(
        user_id,
        reference,
        view,
        source="autonomous",
        reason=reason[:200] if isinstance(reason, str) else "",
    )
    return {
        "expressed": result.get("outcome") == "queued",
        "action_id": selected.action_id if result.get("outcome") == "queued" else None,
        "reason": result.get("message", ""),
    }
