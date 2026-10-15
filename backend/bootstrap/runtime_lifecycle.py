"""集中登记模块的准备、恢复、维护与关闭能力。"""

from entrypoints.desktop.handlers import drain as drain_user_sessions
from entrypoints.scheduler.cron import drain as drain_cron
from entrypoints.scheduler.cron import invalidate_user_scheduler_state, stop_scheduler
from features.actions.lifecycle import runtime_participants as action_participants
from features.appearance.lifecycle import runtime_participants as appearance_participants
from features.companion.lifecycle import runtime_participants as companion_participants
from features.conversation.submissions import recover_interrupted_submissions
from features.desktop_life.lifecycle import runtime_participants as desktop_life_participants
from features.media.lifecycle import runtime_participants as media_participants
from features.memory.lifecycle import runtime_participants as memory_participants
from features.posts.lifecycle import runtime_participants as post_participants
from features.scenes.lifecycle import runtime_participants as scene_participants
from features.updates.lifecycle import runtime_participants as update_participants
from runtime.events.loop import drain_event_tasks, stop_event_loop
from runtime.lifecycle import RUNTIME_LIFECYCLE, RuntimeParticipant
from workflows.onboarding.initial_appearance import resume_initial_appearance


def configure_runtime_lifecycle() -> None:
    RUNTIME_LIFECYCLE.configure(
        (
            *update_participants(),
            *media_participants(),
            *action_participants(),
            *desktop_life_participants(),
            *appearance_participants(),
            *scene_participants(),
            RuntimeParticipant("onboarding.initial_assets", recover=resume_initial_appearance),
            *post_participants(),
            *companion_participants(),
            *memory_participants(),
            RuntimeParticipant("chat.submissions", prepare=recover_interrupted_submissions),
            RuntimeParticipant("desktop.connections", settle=drain_user_sessions),
            RuntimeParticipant(
                "scheduler",
                invalidate_user=invalidate_user_scheduler_state,
                stop_producer=stop_scheduler,
                drain=drain_cron,
            ),
            RuntimeParticipant("events", stop_producer=stop_event_loop, drain=drain_event_tasks),
        ),
    )
