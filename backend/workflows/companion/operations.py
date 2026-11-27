from features.companion.intents import contact_recipients, queue_companion_intent
from features.companion.proactive_runtime import (
    PresentationSnapshot,
    get_presentation_snapshot,
    observe_companion_presence,
)
from features.visuals.repository import VisualError, notify_visuals_updated
from features.visuals.service import restore_presentation
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.events.contracts import COMPANION_TURN_EVENT, COMPANION_TURN_EVENTS
from runtime.events.loop import interrupt_user_event_tasks

logger = get_logger(__name__)

_restored_presentations: dict[int, PresentationSnapshot] = {}


def forget_companion_state(user_id: int) -> None:
    _restored_presentations.pop(user_id, None)


async def interrupt_unavailable_companion_turns(user_id: int) -> None:
    desktop, remote = contact_recipients(user_id)
    if not desktop:
        unavailable = (COMPANION_TURN_EVENT,) if remote else COMPANION_TURN_EVENTS
        await interrupt_user_event_tasks(user_id, *unavailable)


async def companion_signal(user_id: int, params: dict) -> dict[str, bool]:
    available = params["available"]
    event = params.get("event")
    became_available = await observe_companion_presence(
        user_id, available, presentation_mode=params["presentation_mode"], visual_surface=params.get("visual_surface")
    )
    if not available:
        await interrupt_unavailable_companion_turns(user_id)
    presentation = get_presentation_snapshot(user_id)
    if (
        presentation is not None
        and presentation.surface != "none"
        and _restored_presentations.get(user_id) != presentation
    ):
        try:
            await restore_presentation(user_id)
        except VisualError:
            logger.warning("could not restore existing presentation", extra={"user_id": user_id}, exc_info=True)
        else:
            if get_presentation_snapshot(user_id) == presentation:
                # 本地候选没有正式恢复指令，宿主也需要读取新代次并重新确认首帧。
                async with SESSION_LOCAL() as db:
                    await notify_visuals_updated(db, user_id)
                    await db.commit()
                if get_presentation_snapshot(user_id) == presentation:
                    _restored_presentations[user_id] = presentation
    if not available:
        return {"queued": False}
    queued = await queue_companion_intent(user_id, "desktop_available" if became_available else event)
    if became_available and event == "context_changed":
        queued = await queue_companion_intent(user_id, event) or queued
    return {"queued": queued}
