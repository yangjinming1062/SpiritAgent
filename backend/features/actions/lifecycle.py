"""窗口动作制作、评审与回收的运行生命周期。"""

from runtime.lifecycle import RuntimeParticipant

from .asset_retirement import cleanup_retired_action_assets
from .generation.tasks import drain_video_generation, resume_user_dynamic_actions, resume_video_generation_jobs
from .pipeline import drain_proposal_reviews, resume_proposal_reviews


def runtime_participants() -> tuple[RuntimeParticipant, ...]:
    return (
        RuntimeParticipant("actions.retired_assets", prepare=cleanup_retired_action_assets),
        RuntimeParticipant(
            "actions.generation",
            recover=resume_video_generation_jobs,
            resume_user=resume_user_dynamic_actions,
            drain=drain_video_generation,
        ),
        RuntimeParticipant(
            "actions.proposal_reviews",
            recover=resume_proposal_reviews,
            resume_user=resume_proposal_reviews,
            drain=drain_proposal_reviews,
        ),
    )
