from features.companion.intents import queue_companion_intent
from features.companion.proactive_runtime import (
    PresentationSnapshot,
    get_presentation_snapshot,
    observe_companion_presence,
)
from features.visuals.repository import VisualError
from features.visuals.service import restore_presentation
from infrastructure.logger import get_logger
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks

logger = get_logger(__name__)

_restored_presentations: dict[int, PresentationSnapshot] = {}


def forget_companion_state(user_id: int) -> None:
    _restored_presentations.pop(user_id, None)


async def companion_signal(user_id: int, params: dict) -> dict[str, bool]:
    available = params["available"]
    event = params.get("event")
    became_available = observe_companion_presence(user_id, available, presentation_mode=params["presentation_mode"])
    if not available:
        await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
        return {"queued": False}
    presentation = get_presentation_snapshot(user_id)
    if presentation is not None and _restored_presentations.get(user_id) != presentation:
        try:
            await restore_presentation(user_id)
        except VisualError:
            logger.warning("could not restore existing presentation", extra={"user_id": user_id}, exc_info=True)
        else:
            if get_presentation_snapshot(user_id) == presentation:
                _restored_presentations[user_id] = presentation
    queued = await queue_companion_intent(user_id, "desktop_available" if became_available else event)
    if became_available and event == "context_changed":
        queued = await queue_companion_intent(user_id, event) or queued
    return {"queued": queued}
