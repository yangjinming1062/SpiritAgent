"""公共媒体任务凭持久句柄或产物恢复，不重提结果未知的制作。"""

from runtime.lifecycle import RuntimeParticipant

from .continuations import reconcile_media_waits
from .image_jobs import recover_image_jobs
from .manual_reviews import drain_manual_operations, recover_manual_operations
from .review_operations import recover_review_attempts
from .video_jobs import drain, resume_pending_video_jobs, resume_user_video_jobs


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant("media.image_jobs", recover=recover_image_jobs),
        RuntimeParticipant("media.manual_operations", recover=recover_manual_operations, drain=drain_manual_operations),
        RuntimeParticipant("media.review_attempts", prepare=recover_review_attempts),
        RuntimeParticipant("media.continuations", recover=reconcile_media_waits, resume_user=reconcile_media_waits),
        RuntimeParticipant(
            "media.video_jobs", recover=resume_pending_video_jobs, resume_user=resume_user_video_jobs, drain=drain
        ),
    )
