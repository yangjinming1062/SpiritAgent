import time

from features.companion.disturbance import get_disturbance_tier
from features.companion.intents import queue_companion_intent
from features.companion.proactive_runtime import observe_companion_presence
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks
from workflows.visual_decisions import evaluate_visuals

logger = get_logger(__name__)

_last_idle_expression_ts: dict[int, float] = {}


def forget_companion_state(user_id: int) -> None:
    _last_idle_expression_ts.pop(user_id, None)


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """若用户仍在窗口内返回 True 且不更新时间戳。"""
    return now - state.get(user_id, 0.0) < min_interval


async def companion_idle_expression(user_id: int, params: dict) -> dict:
    trigger = params.get("trigger", "periodic")
    if await get_disturbance_tier(user_id) != "autonomous":
        return {"expressed": False, "action_id": None, "reason": "autonomous tier required"}
    now = time.monotonic()
    if _user_throttled(
        _last_idle_expression_ts,
        user_id,
        SETTINGS.companion_idle_expression_min_interval_seconds,
        now,
    ):
        logger.debug(
            "idle_expression: throttled",
            extra={"user_id": user_id, "since_sec": round(now - _last_idle_expression_ts.get(user_id, 0.0), 3)},
        )
        return {"expressed": False, "action_id": None, "reason": "throttled"}
    _last_idle_expression_ts[user_id] = now

    return await evaluate_visuals(user_id, trigger=trigger, allow_create=True)


async def companion_signal(user_id: int, params: dict) -> dict[str, bool]:
    available = params["available"]
    event = params.get("event")
    became_available = observe_companion_presence(
        user_id,
        available,
        presentation_mode=params["presentation_mode"],
    )
    if not available:
        await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
        return {"queued": False}
    queued = await queue_companion_intent(user_id, "desktop_available" if became_available else event)
    if became_available and event == "context_changed":
        queued = await queue_companion_intent(user_id, event) or queued
    return {"queued": queued}
