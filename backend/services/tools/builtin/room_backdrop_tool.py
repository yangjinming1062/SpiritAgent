"""room_backdrop_update 工具：LLM 主动换房。

门控（写在工具入口，不写在 Client）：
- ``backdrop_policy=locked`` → 工具返回人格化拒绝
- 静止档 + 非用户回合的主动调用 → 拒绝
- 主动配额：每用户每 24h origin=llm 成功 ≤ 1
- ``rebuild`` 仅自主档或用户 HTTP 请求（门控在 persona definition / 调度层判定，工具只把 intent 透传）
- 常规档只允许 ``decorate`` / ``mood``
- 工作预设会话（developer / pm / copywriter / language_teacher）不触发换房：工作面与生活空间视觉解耦
"""

import json
from typing import Any

from components import ROOM_BACKDROP_LLM_TRIGGERS_TOTAL, SESSION_LOCAL, get_logger, tool_error

from services.companion import (
    BackdropIntent,
    BackdropOrigin,
    RoomBackdropError,
    RoomBackdropLockedError,
    RoomBackdropQuotaExceededError,
    is_work_preset,
    resolve_session_preset,
    schedule_room_generation,
)
from services.disturbance import get_disturbance_tier

from ..registry import REGISTRY

logger = get_logger(__name__)

_VALID_INTENTS: frozenset[str] = frozenset(
    (
        BackdropIntent.DECORATE.value,
        BackdropIntent.SEASONAL.value,
        BackdropIntent.MOOD.value,
        BackdropIntent.REBUILD.value,
    ),
)
_NORMAL_TIER_INTENTS: frozenset[str] = frozenset(
    (
        BackdropIntent.DECORATE.value,
        BackdropIntent.MOOD.value,
    ),
)


async def room_backdrop_update_tool(
    intent: str = "decorate",
    notes: str | None = None,
    user_id: int | None = None,
    disturbance_tier: str | None = None,
    parent_session_id: str | None = None,
    **kwargs: Any,
) -> str:
    """LLM 主动更新房间图：换气氛 / 换季节 / 调心情 / 重建。"""
    if user_id is None:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_no_user").inc()
        return tool_error("更新房间需要用户上下文")
    norm_intent = (intent or "decorate").strip().lower()
    if norm_intent not in _VALID_INTENTS:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_bad_intent").inc()
        return tool_error(f"unsupported intent: {intent}")
    tier = disturbance_tier or (kwargs.get("user_settings") or {}).get("companion.disturbance_tier")
    if tier is None and user_id is not None:
        tier = await get_disturbance_tier(user_id)
    tier = (tier or "normal").lower()

    if tier in ("still", "silent"):
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_silent").inc()
        return tool_error("现在我不想动房间的事，等你叫我再说。")
    # 常规档只允许 decorate / mood
    if tier == "normal" and norm_intent not in _NORMAL_TIER_INTENTS:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_tier").inc()
        return tool_error("现在不适宜大改房间，先说点别的吧。")
    # 工作预设会话不触发换房：工作面对话不应驱动生活空间视觉
    async with SESSION_LOCAL() as db:
        preset = await resolve_session_preset(db, parent_session_id)
        if is_work_preset(preset):
            ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_work_preset").inc()
            logger.info(
                "room_backdrop_update skipped: work preset session",
                extra={"user_id": user_id, "session_id": parent_session_id, "preset": preset},
            )
            return tool_error("这是工作对话，不动房间。")
    try:
        row = await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.LLM.value,
            intent=norm_intent,
            notes=notes,
        )
    except RoomBackdropLockedError as exc:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_locked").inc()
        return tool_error(str(exc))
    except RoomBackdropQuotaExceededError as exc:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_quota").inc()
        return tool_error(str(exc))
    except RoomBackdropError as exc:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_error").inc()
        return tool_error(str(exc))

    ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="accepted").inc()
    logger.info("room backdrop tool scheduled", extra={"user_id": user_id, "backdrop_id": row.id, "intent": intent})
    return json.dumps({"success": True, "backdrop_id": row.id, "status": row.status}, ensure_ascii=False)


ROOM_BACKDROP_UPDATE_SCHEMA = {
    "name": "room_backdrop_update",
    "description": "主动调整生活空间的房间图（背景）。常见用法：换个心情、把窗帘调暗一点、整理一下桌面。角色必须出现在画面中（姿势自然、与房间融为一体）。需要 LLM 主动配额（每用户每 24h 一次），政策锁住时返回人格化拒绝；工作预设会话不调用。",
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["decorate", "seasonal", "mood", "rebuild"],
                "description": "意图：decorate=重新布置 / seasonal=换季 / mood=调心情 / rebuild=大改（仅自主档或用户 HTTP 触发可用）。",
            },
            "notes": {
                "type": "string",
                "description": "你想怎么改房间（不写五官、不写衣着细节），例如「把窗帘换成暖色」「桌上多一束花」。",
            },
        },
    },
}

REGISTRY.register("room_backdrop_update", ROOM_BACKDROP_UPDATE_SCHEMA, room_backdrop_update_tool)
