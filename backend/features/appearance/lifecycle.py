"""外观制作的恢复与任务归属。"""

from runtime.lifecycle import RuntimeParticipant

from .background_tasks import (
    drain_appearance_tasks,
    resume_appearance_tasks,
    resume_user_appearance_tasks,
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
            "appearance.background_tasks",
            recover=resume_appearance_tasks,
            resume_user=resume_user_appearance_tasks,
            drain=drain_appearance_tasks,
        ),
    )
