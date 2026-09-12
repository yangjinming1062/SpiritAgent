"""伙伴业务域：身份、心情、互动规则、外观状态读模型、打扰档位与音色目录。"""

from services.domains.companion.affect_check import AffectCheckResult, check_affect
from services.domains.companion.affect_emit import emit_companion_affect, emit_companion_message
from services.domains.companion.appearance import build_outfit_extras, get_active_model
from services.domains.companion.disturbance import (
    ALLOWED_TIERS,
    DEFAULT_TIER,
    TIER_SETTING_KEY,
    get_disturbance_tier,
    is_still,
)
from services.domains.companion.emotions import BUILTIN_EMOTIONS
from services.domains.companion.interact import REGION_NAMES_ZH, InteractResult, interact
from services.domains.companion.interaction_stats import read_today_summary, record_interaction
from services.domains.companion.mood import (
    emit_companion_mood,
    normalize_mood,
    update_mood_from_companion_turn,
)
from services.domains.companion.persona_background import drain as drain_persona_background
from services.domains.companion.persona_background import schedule_personality_tag_refresh
from services.domains.companion.persona_service import (
    ONBOARDING_FIELDS,
    PersonaValidationError,
    build_system_prompt_extras,
    confirm_portrait,
    get_onboarding_state,
    get_or_create_persona,
    load_persona_definition,
    normalize_persona_aliases,
    render_extras,
    submit_onboarding_field,
    update_persona,
)
from services.domains.companion.personality_tagger import analyze_personality_tags
from services.domains.companion.prompt_runtime import run_prompt_json
from services.domains.companion.rig_type_selector import classify_species, select_rig_type
from services.domains.companion.session_preset import is_life_preset, is_work_preset, resolve_session_preset
from services.domains.companion.should_act import ALLOWED_ACTIONS, ShouldActResult, should_act
from services.domains.companion.voice_catalog import (
    design_voice,
    list_tts_voices,
    match_user_voice,
    normalize_voice_language,
)

__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_TIERS",
    "AffectCheckResult",
    "BUILTIN_EMOTIONS",
    "DEFAULT_TIER",
    "InteractResult",
    "ONBOARDING_FIELDS",
    "PersonaValidationError",
    "REGION_NAMES_ZH",
    "ShouldActResult",
    "TIER_SETTING_KEY",
    "analyze_personality_tags",
    "build_outfit_extras",
    "build_system_prompt_extras",
    "check_affect",
    "classify_species",
    "confirm_portrait",
    "design_voice",
    "drain_persona_background",
    "emit_companion_affect",
    "emit_companion_message",
    "emit_companion_mood",
    "get_active_model",
    "get_disturbance_tier",
    "get_onboarding_state",
    "get_or_create_persona",
    "interact",
    "is_life_preset",
    "is_still",
    "is_work_preset",
    "list_tts_voices",
    "load_persona_definition",
    "match_user_voice",
    "normalize_mood",
    "normalize_persona_aliases",
    "normalize_voice_language",
    "read_today_summary",
    "record_interaction",
    "render_extras",
    "resolve_session_preset",
    "run_prompt_json",
    "schedule_personality_tag_refresh",
    "select_rig_type",
    "should_act",
    "submit_onboarding_field",
    "update_mood_from_companion_turn",
    "update_persona",
]
