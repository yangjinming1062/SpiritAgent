"""集中登记模块的准备、恢复、维护与关闭能力。"""

from entrypoints.desktop.handlers import drain as drain_user_sessions
from entrypoints.scheduler.cron import stop_scheduler
from features.action_library.lifecycle import runtime_participants as action_participants
from features.appearance.lifecycle import runtime_participants as appearance_participants
from features.companion.lifecycle import runtime_participants as companion_participants
from features.configuration.social_accounts import runtime_participants as social_account_participants
from features.conversation.submissions import recover_interrupted_submissions
from features.media.lifecycle import runtime_participants as media_participants
from features.media.video_queue.lifecycle import runtime_participants as video_queue_participants
from features.memory.lifecycle import runtime_participants as memory_participants
from features.posts.lifecycle import runtime_participants as post_participants
from features.scenes.lifecycle import runtime_participants as scene_participants
from features.updates.lifecycle import runtime_participants as update_participants
from features.visuals.lifecycle import runtime_participants as visuals_participants
from runtime.events.loop import drain_event_tasks, stop_event_loop
from runtime.lifecycle import RUNTIME_LIFECYCLE, RuntimeParticipant
from workflows.onboarding.initial_appearance import resume_initial_appearance
from workflows.scheduling import drain as drain_scheduling
from workflows.scheduling import invalidate_user_scheduler_state
from workflows.visual_decisions import drain_visual_decisions


def configure_runtime_lifecycle() -> None:
    RUNTIME_LIFECYCLE.configure(
        (
            *update_participants(),
            *video_queue_participants(),
            *media_participants(),
            *action_participants(),
            *visuals_participants(),
            RuntimeParticipant("visuals.decisions", drain=drain_visual_decisions),
            *appearance_participants(),
            *scene_participants(),
            RuntimeParticipant("onboarding.initial_assets", recover=resume_initial_appearance),
            *post_participants(),
            *companion_participants(),
            *social_account_participants(),
            *memory_participants(),
            RuntimeParticipant("chat.submissions", prepare=recover_interrupted_submissions),
            RuntimeParticipant("desktop.connections", settle=drain_user_sessions),
            RuntimeParticipant(
                "scheduler",
                invalidate_user=invalidate_user_scheduler_state,
                stop_producer=stop_scheduler,
                drain=drain_scheduling,
            ),
            RuntimeParticipant("events", stop_producer=stop_event_loop, drain=drain_event_tasks),
        ),
    )
