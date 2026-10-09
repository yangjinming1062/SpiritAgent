"""动作应用流程：提案受理、独立评审与播放协调。"""

from services.domains.actions import DesktopVideoError, DesktopVideoNotFoundError, DesktopVideoStateError

from .context import ActionContextSnapshot, build_action_context
from .design import ProposalAcceptance, accept_proposal
from .desktop import (
    build_desktop_action_context,
    choose_idle_desktop_action,
    claim_desktop_playback,
    design_desktop_action,
    drain_desktop_reviews,
    ensure_current_desktop_videos,
    generate_desktop_action,
    get_desktop_action_response,
    get_desktop_proposal_response,
    get_desktop_video_state,
    list_desktop_video_sets,
    play_desktop_action,
    record_desktop_playback,
    resume_desktop_reviews,
    review_desktop_action,
    set_desktop_video_preferences,
)
from .pipeline import drain_proposal_reviews, resume_proposal_reviews, schedule_accepted_proposal
from .playback import request_playback

__all__ = [
    "DesktopVideoError",
    "DesktopVideoNotFoundError",
    "DesktopVideoStateError",
    "build_desktop_action_context",
    "choose_idle_desktop_action",
    "claim_desktop_playback",
    "design_desktop_action",
    "drain_desktop_reviews",
    "ensure_current_desktop_videos",
    "generate_desktop_action",
    "get_desktop_action_response",
    "get_desktop_proposal_response",
    "get_desktop_video_state",
    "list_desktop_video_sets",
    "play_desktop_action",
    "record_desktop_playback",
    "review_desktop_action",
    "resume_desktop_reviews",
    "set_desktop_video_preferences",
    "ActionContextSnapshot",
    "ProposalAcceptance",
    "accept_proposal",
    "build_action_context",
    "drain_proposal_reviews",
    "request_playback",
    "resume_proposal_reviews",
    "schedule_accepted_proposal",
]
