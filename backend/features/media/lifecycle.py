"""公共媒体任务凭持久句柄或产物恢复，不重提结果未知的制作。"""

from runtime.lifecycle import RuntimeParticipant

from .video_jobs import drain, resume_pending_video_jobs, resume_user_video_jobs


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "media.video_jobs",
            recover=resume_pending_video_jobs,
            resume_user=resume_user_video_jobs,
            drain=drain,
        ),
    )
