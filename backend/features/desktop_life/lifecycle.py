"""桌面生活恢复保留启动与维护后的不同模型调用边界。"""

from runtime.lifecycle import RuntimeParticipant

from .generation import drain_desktop_video_jobs, resume_desktop_video_jobs
from .naming import drain_desktop_title_jobs, resume_desktop_title_jobs, stop_desktop_title_jobs


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "desktop_life.generation",
            recover=resume_desktop_video_jobs,
            resume_user=resume_desktop_video_jobs,
            drain=drain_desktop_video_jobs,
        ),
        RuntimeParticipant(
            "desktop_life.naming",
            recover=resume_desktop_title_jobs,
            resume_user=resume_desktop_title_jobs,
            stop_producer=stop_desktop_title_jobs,
            drain=drain_desktop_title_jobs,
        ),
    )
