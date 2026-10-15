"""桌面生活恢复保留启动与维护后的不同模型调用边界。"""

from runtime.lifecycle import RuntimeParticipant

from .generation import drain_desktop_video_jobs, resume_desktop_video_jobs
from .service import drain_desktop_reviews, resume_desktop_reviews


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "desktop_life.generation",
            recover=resume_desktop_video_jobs,
            resume_user=resume_desktop_video_jobs,
            drain=drain_desktop_video_jobs,
        ),
        RuntimeParticipant(
            "desktop_life.reviews",
            recover=resume_desktop_reviews,
            resume_user=resume_desktop_reviews,
            drain=drain_desktop_reviews,
        ),
    )
