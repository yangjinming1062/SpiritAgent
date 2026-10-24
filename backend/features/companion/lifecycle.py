"""伙伴后台任务与账户运行镜像的生命周期。"""

from runtime.lifecycle import RuntimeParticipant

from .first_greeting import drain as drain_first_greeting
from .interaction_stats import invalidate_user_interaction_stats
from .persona_background import drain as drain_persona_background
from .proactive_runtime import clear_user_proactive_state


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant("companion.first_greeting", drain=drain_first_greeting),
        RuntimeParticipant("companion.persona_background", drain=drain_persona_background),
        RuntimeParticipant("companion.proactive", invalidate_user=clear_user_proactive_state),
        RuntimeParticipant("companion.interaction_stats", invalidate_user=invalidate_user_interaction_stats),
    )
