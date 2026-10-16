import time

from features.actions.playback import request_playback
from features.actions.schemas import ActionPlayRequest
from features.companion.affect_emit import emit_companion_message
from features.companion.disturbance import get_disturbance_tier
from features.companion.idle_expression import check_idle_expression
from features.companion.intents import queue_companion_intent
from features.companion.proactive_runtime import (
    get_presentation_snapshot,
    get_user_proactive_record,
    observe_companion_presence,
)
from features.companion.should_act import should_act
from features.desktop_life.service import choose_idle_desktop_action
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import coerce_hour_0_23, coerce_non_negative_float
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks
from runtime.gateway_contracts import GatewaySession, RegisterMethod
from workflows.chat.gateway_inputs import _resolve_llm_config

logger = get_logger(__name__)

_last_idle_expression_ts: dict[int, float] = {}
_last_should_act_ts: dict[int, float] = {}
SHOULD_ACT_ANTIDUP_SECONDS = 2.0


def forget_gateway_state(user_id: int) -> None:
    _last_idle_expression_ts.pop(user_id, None)
    _last_should_act_ts.pop(user_id, None)


def _user_throttled(state: dict[int, float], user_id: int, min_interval: float, now: float) -> bool:
    """若用户仍在窗口内返回 True 且不更新时间戳。"""
    return now - state.get(user_id, 0.0) < min_interval


def register_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    user_id = session.user_id

    async def companion_idle_expression(params: dict) -> dict:
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

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        presentation = get_presentation_snapshot(user_id)
        contact_revision = get_user_proactive_record(user_id).contact_revision
        if presentation is not None and presentation.mode == "desktop":
            return await choose_idle_desktop_action(user_id, idle_seconds, local_hour, trigger=trigger)
        if trigger == "user_return":
            return {"expressed": False, "action_id": None, "reason": "desktop required"}
        llm_config = await _resolve_llm_config(user_id, AiTaskKey.proactive)
        result = await check_idle_expression(user_id, idle_seconds, local_hour, llm_config)
        if (
            presentation != get_presentation_snapshot(user_id)
            or contact_revision != get_user_proactive_record(user_id).contact_revision
            or await get_disturbance_tier(user_id) != "autonomous"
        ):
            return {"expressed": False, "action_id": None, "reason": "presentation changed"}
        if result.expressed and result.action_id is not None:
            async with SESSION_LOCAL() as db:
                play = await request_playback(
                    db,
                    user_id,
                    ActionPlayRequest(action_id=result.action_id, reason="idle expression"),
                    source="autonomous",
                )
                if (
                    presentation != get_presentation_snapshot(user_id)
                    or contact_revision != get_user_proactive_record(user_id).contact_revision
                ):
                    return {"expressed": False, "action_id": None, "reason": "presentation changed"}
                await db.commit()
            return {
                "expressed": play.outcome == "queued",
                "action_id": result.action_id,
                "reason": play.message or result.reason,
            }
        return {
            "expressed": False,
            "action_id": None,
            "reason": result.reason,
        }

    register("companion.idle_expression", companion_idle_expression)

    async def companion_signal(params: dict) -> dict[str, bool]:
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

    register("companion.signal", companion_signal)

    async def companion_should_act(params: dict) -> dict:
        # 空间自主决策只服务自主档；客户端另以桌面精灵可见性拦截。
        if await get_disturbance_tier(user_id) != "autonomous":
            return {"should_act": False, "action": "stay", "reason": "autonomous tier required"}

        presentation = get_presentation_snapshot(user_id)
        if presentation is not None and presentation.mode == "desktop":
            return {"should_act": False, "action": "stay", "reason": "window presentation required"}

        now = time.monotonic()
        if _user_throttled(_last_should_act_ts, user_id, SHOULD_ACT_ANTIDUP_SECONDS, now):
            return {"should_act": False, "action": "stay", "reason": "throttled"}
        _last_should_act_ts[user_id] = now

        idle_seconds = coerce_non_negative_float(params.get("idle_seconds"))
        local_hour = coerce_hour_0_23(params.get("local_hour"))
        focused_category = params.get("focused_category")
        fullscreen = bool(params.get("fullscreen"))
        screen_locked = bool(params.get("screen_locked"))
        seconds_since_last_action = coerce_non_negative_float(params.get("seconds_since_last_action"))

        presentation = get_presentation_snapshot(user_id)
        contact_revision = get_user_proactive_record(user_id).contact_revision
        llm_config = await _resolve_llm_config(user_id, AiTaskKey.proactive)
        res = await should_act(
            user_id=user_id,
            idle_seconds=idle_seconds,
            local_hour=local_hour,
            focused_category=focused_category,
            fullscreen=fullscreen,
            screen_locked=screen_locked,
            seconds_since_last_action=seconds_since_last_action,
            llm_config=llm_config,
        )
        if (
            presentation != get_presentation_snapshot(user_id)
            or contact_revision != get_user_proactive_record(user_id).contact_revision
            or await get_disturbance_tier(user_id) != "autonomous"
        ):
            return {"should_act": False, "action": "stay", "reason": "presentation changed"}
        # 走过去搭话（DESIGN「位置、移动与缩放」）：开场白经 companion.message 独立投递、客户端边走边说，RPC 响应只承载走位动作；should_act 已把 approach 的 params 收敛为非空 text。
        if (
            res.action == "approach"
            and res.params is not None
            and not await emit_companion_message(user_id, res.params["text"], presentation=presentation)
        ):
            return {"should_act": False, "action": "stay", "reason": "presentation changed"}
        return res.model_dump()

    register("companion.should_act", companion_should_act)
