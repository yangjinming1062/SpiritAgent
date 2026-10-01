"""动作应用流程：提案受理、独立评审与播放协调。"""

from .context import ActionContextSnapshot, build_action_context
from .design import ProposalAcceptance, accept_proposal
from .pipeline import drain_proposal_reviews, resume_proposal_reviews, schedule_accepted_proposal
from .playback import request_playback

__all__ = [
    "ActionContextSnapshot",
    "ProposalAcceptance",
    "accept_proposal",
    "build_action_context",
    "drain_proposal_reviews",
    "request_playback",
    "resume_proposal_reviews",
    "schedule_accepted_proposal",
]
