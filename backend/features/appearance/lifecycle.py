"""外观制作的恢复与任务归属。"""

from runtime.lifecycle import RuntimeParticipant

from .character_extraction import (
    drain_character_extractions,
    resume_character_extractions,
    resume_user_character_extraction,
)
from .outfit_service import drain_outfit_descriptions, recover_outfit_descriptions


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "appearance.outfit_descriptions",
            prepare=recover_outfit_descriptions,
            drain=drain_outfit_descriptions,
        ),
        RuntimeParticipant(
            "appearance.character_extraction",
            recover=resume_character_extractions,
            resume_user=resume_user_character_extraction,
            drain=drain_character_extractions,
        ),
    )
