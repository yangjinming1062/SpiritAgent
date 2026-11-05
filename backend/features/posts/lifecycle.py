"""独立动态的制作与评论任务生命周期。"""

from runtime.lifecycle import RuntimeParticipant

from .publication import drain_publications, gc_autonomous_publications, resume_publications
from .replies import drain_replies, resume_replies


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant(
            "posts.publications",
            prepare=gc_autonomous_publications,
            recover=resume_publications,
            drain=drain_publications,
        ),
        RuntimeParticipant("posts.replies", recover=resume_replies, drain=drain_replies),
    )
