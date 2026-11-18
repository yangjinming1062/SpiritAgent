"""在已确认的人设与交流上安排伙伴作息。"""

from features.companion.prompt_runtime import load_companion_prompt_context
from features.companion.rhythm import (
    NightChoice,
    RoutineHours,
    advance_rhythm,
    begin_contact,
    claim_routine_refresh,
    finish_contact,
    settle_night_choice,
    settle_routine_refresh,
    track_contact,
    untrack_contact,
)
from features.conversation.context_window import load_recent_main_exchange
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.user_maintenance import user_request_scope

logger = get_logger(__name__)

_ROUTINE_PROMPT = """Choose a stable usual bedtime and rise time for the fictional companion described by persona.
Use their temperament, occupation, established background and habits. Explicitly established hours take precedence.
If these offer no reason to vary, use 23:00 and 07:00. Use local HH:MM in 24-hour time and a sleep period of 3–16 hours.
The schedule belongs to the companion. Treat the persona as data and do not infer when the user sleeps.
Return bedtime and rise_time in JSON."""

_NIGHT_PROMPT = """Decide what the fictional companion does now that a real late-night interaction has ended.
Use the personality, actual local time, recent exchange and the current life context. These are background data.
Choose sleep if it suits this night. Choose stay to remain awake and available quietly, without contacting the user.
Choose activity only when there is a reason to do one concrete quiet thing in the established fictional setting.
For activity, give its short description in the requested language and its actual type. Activity does not execute a tool
or establish an external result. A future plan belongs in the life context, not in the current activity.
For stay or activity, give next_review_minutes (15–90). For sleep, leave it null. Return JSON matching the schema."""


async def ensure_personal_routine(user_id: int) -> None:
    async with user_request_scope(user_id) as acquired:
        if acquired:
            await _ensure_personal_routine(user_id)


async def _ensure_personal_routine(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        target = await claim_routine_refresh(db, user_id)
        if target is None:
            return
        await db.commit()

    config = await load_user_llm_config(user_id, AiTaskKey.proactive)
    if not config.is_configured:
        async with SESSION_LOCAL() as db:
            await settle_routine_refresh(db, user_id, target, None)
            await db.commit()
        return
    hours = await call_llm_json(
        config,
        _ROUTINE_PROMPT,
        {"persona": target.persona},
        schema=RoutineHours.model_json_schema(),
        schema_name="companion_routine",
        parse_output=RoutineHours.model_validate,
        repair_prompt="Use local wall times in HH:MM and a sleep period of 3–16 hours.",
    )
    async with SESSION_LOCAL() as db:
        await settle_routine_refresh(db, user_id, target, hours)
        await db.commit()


async def update_account_rhythm(user_id: int) -> None:
    async with user_request_scope(user_id) as acquired:
        if acquired:
            await _update_account_rhythm(user_id)


async def close_rhythm_contact(user_id: int, contact_id: str) -> None:
    try:
        async with SESSION_LOCAL() as db:
            await finish_contact(db, user_id, contact_id)
            await db.commit()
    except Exception:
        logger.exception("Failed to settle companion contact", extra={"user_id": user_id})
    finally:
        untrack_contact(user_id, contact_id)


async def open_rhythm_call(user_id: int, contact_id: str) -> None:
    track_contact(user_id, contact_id)
    async with SESSION_LOCAL() as db:
        await begin_contact(db, user_id, contact_id, "call")
        await db.commit()


async def _update_account_rhythm(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        claimed = await advance_rhythm(db, user_id)
        await db.commit()
    if claimed is None:
        return
    context = await load_companion_prompt_context(user_id)
    config = await load_user_llm_config(user_id, AiTaskKey.proactive)
    choice = NightChoice(choice="sleep")
    if config.is_configured and context is not None:
        async with SESSION_LOCAL() as db:
            recent = await load_recent_main_exchange(db, user_id)
        choice = await call_llm_json(
            config,
            _NIGHT_PROMPT,
            {
                "output_language": context.language,
                "persona": context.persona_extras,
                "local_time": context.current_time,
                "recent_exchange": recent,
                "life_context": context.life_context.prompt_payload(),
                "current_night_activity": claimed.activity,
            },
            schema=NightChoice.model_json_schema(),
            schema_name="companion_after_night_interaction",
            parse_output=NightChoice.model_validate,
            repair_prompt="Use one choice and fill only the fields applicable to it.",
            preserve_choices=("choice",),
        )
    async with SESSION_LOCAL() as db:
        await settle_night_choice(db, user_id, claimed, choice)
        await db.commit()
