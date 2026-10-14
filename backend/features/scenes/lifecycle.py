"""场景制作的恢复与任务归属。"""

from runtime.lifecycle import RuntimeParticipant

from .service import drain_scene_jobs, resume_scene_jobs, resume_user_scene_jobs


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "scenes.generation",
            recover=resume_scene_jobs,
            resume_user=resume_user_scene_jobs,
            drain=drain_scene_jobs,
        ),
    )
