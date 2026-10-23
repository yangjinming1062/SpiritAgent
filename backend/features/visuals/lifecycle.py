"""影像库恢复保留启动与维护后的不同模型调用边界。"""

from runtime.lifecycle import RuntimeParticipant

from .generation import drain, resume_visual_jobs
from .naming import drain_visual_title_jobs, resume_visual_title_jobs, stop_visual_title_jobs


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "visuals.generation",
            recover=resume_visual_jobs,
            resume_user=resume_visual_jobs,
            drain=drain,
        ),
        RuntimeParticipant(
            "visuals.naming",
            recover=resume_visual_title_jobs,
            resume_user=resume_visual_title_jobs,
            stop_producer=stop_visual_title_jobs,
            drain=drain_visual_title_jobs,
        ),
    )
