"""伙伴业务域：身份、心情、互动规则、外观状态读模型、打扰档位与音色目录。"""

from .affect_emit import emit_companion_message
from .appearance import build_outfit_extras
from .character_card import (
    CharacterCardConflictError,
    CharacterCardNotReadyError,
    character_card_response,
    character_snapshot_is_current,
    emit_character_card_updated,
    get_character_card,
    load_character_snapshot,
    register_character_card,
    render_character_identity,
    render_character_profile,
    request_character_extraction,
    require_character_snapshot,
    update_character_card,
)
from .disturbance import (
    get_disturbance_tier,
    is_still,
)
from .emotions import BUILTIN_EMOTIONS
from .idle_expression import IdleExpressionResult, check_idle_expression
from .intents import (
    begin_companion_intent,
    cancel_companion_wait,
    companion_turn_plan,
    enqueue_companion_intent,
    finish_companion_intent,
    invalidate_cron_companion_intents,
    latest_user_message_id,
    list_companion_intents,
    queue_companion_intent,
    set_companion_wait,
)
from .interaction_stats import invalidate_user_interaction_stats, record_interaction
from .mood import (
    emit_companion_mood,
    normalize_mood,
    update_mood_from_companion_turn,
)
from .persona_background import drain as drain_persona_background
from .persona_background import schedule_personality_tag_refresh
from .persona_service import (
    ONBOARDING_FIELDS,
    PersonaValidationError,
    build_system_prompt_extras,
    confirm_portrait,
    get_onboarding_state,
    get_or_create_persona,
    load_persona_definition,
    render_extras,
    submit_onboarding_field,
    update_persona,
)
from .personality_tagger import analyze_personality_tags
from .proactive_runtime import (
    can_start_companion_turn,
    clear_user_proactive_state,
    get_personality_tags,
    get_user_proactive_record,
    note_outreach_throttle,
    note_user_contact,
    observe_companion_presence,
    user_turn_activity,
)
from .prompt_runtime import load_companion_prompt_context, run_prompt_json
from .scenes import (
    SceneState,
    get_pending_scene,
    get_scene,
    get_scene_state,
    list_scenes,
    response_for_scene,
    scene_environment,
)
from .session_preset import is_work_preset, resolve_session_profile
from .should_act import ALLOWED_ACTIONS, ShouldActResult, invalidate_user_should_act, should_act
from .voice_catalog import (
    design_voice,
    list_tts_voices,
    match_user_voice,
    normalize_voice_language,
)

__all__ = [
    "SceneState",
    "get_pending_scene",
    "get_scene",
    "get_scene_state",
    "list_scenes",
    "response_for_scene",
    "scene_environment",
    "CharacterCardConflictError",
    "CharacterCardNotReadyError",
    "character_card_response",
    "character_snapshot_is_current",
    "emit_character_card_updated",
    "get_character_card",
    "load_character_snapshot",
    "register_character_card",
    "render_character_identity",
    "render_character_profile",
    "request_character_extraction",
    "require_character_snapshot",
    "update_character_card",
    "can_start_companion_turn",
    "clear_user_proactive_state",
    "get_personality_tags",
    "get_user_proactive_record",
    "note_outreach_throttle",
    "note_user_contact",
    "observe_companion_presence",
    "user_turn_activity",
    "begin_companion_intent",
    "cancel_companion_wait",
    "companion_turn_plan",
    "enqueue_companion_intent",
    "finish_companion_intent",
    "invalidate_cron_companion_intents",
    "latest_user_message_id",
    "list_companion_intents",
    "queue_companion_intent",
    "set_companion_wait",
    "ALLOWED_ACTIONS",
    "IdleExpressionResult",
    "BUILTIN_EMOTIONS",
    "ONBOARDING_FIELDS",
    "PersonaValidationError",
    "ShouldActResult",
    "analyze_personality_tags",
    "build_outfit_extras",
    "build_system_prompt_extras",
    "check_idle_expression",
    "confirm_portrait",
    "design_voice",
    "drain_persona_background",
    "emit_companion_message",
    "emit_companion_mood",
    "get_disturbance_tier",
    "get_onboarding_state",
    "get_or_create_persona",
    "invalidate_user_interaction_stats",
    "invalidate_user_should_act",
    "is_still",
    "is_work_preset",
    "list_tts_voices",
    "load_persona_definition",
    "match_user_voice",
    "normalize_mood",
    "normalize_voice_language",
    "record_interaction",
    "render_extras",
    "resolve_session_profile",
    "load_companion_prompt_context",
    "run_prompt_json",
    "schedule_personality_tag_refresh",
    "should_act",
    "submit_onboarding_field",
    "update_mood_from_companion_turn",
    "update_persona",
]
