"""room_backdrop_update 工具：用户要求或 LLM 主动换房。

门控（写在工具入口，不写在 Client）：
- 用户回合允许显式换房请求和附件参考；自主回合继续受政策、档位与配额门控
- 主动配额：每用户每 24h origin=llm 成功 ≤ 1
- 自主调用的常规档只允许 ``decorate`` / ``mood``；用户请求可重建
- 工作预设会话不绑定本工具（回合装配层过滤，见 prompt_presets.LIFE_SPACE_TOOL_NAMES）
"""

import json
from typing import Any

from components import ROOM_BACKDROP_LLM_TRIGGERS_TOTAL, get_logger, tool_error

from services.application.generation import (
    BackdropIntent,
    BackdropOrigin,
    RoomBackdropError,
    RoomBackdropLockedError,
    RoomBackdropQuotaExceededError,
    schedule_room_generation,
)
from services.domains.companion import get_disturbance_tier
from services.infrastructure.tool_runtime import REGISTRY

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
    reference_image_index: int | None = None,
    user_id: int | None = None,
    disturbance_tier: str | None = None,
    user_images: tuple[str, ...] = (),
    user_initiated: bool = False,
    **kwargs: Any,
) -> str:
    """更新房间图；附件只从服务端绑定的当前会话图片中按序号选择。"""
    if user_id is None:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_no_user").inc()
        return tool_error("更新房间需要用户上下文")
    norm_intent = (intent or "decorate").strip().lower()
    if norm_intent not in _VALID_INTENTS:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_bad_intent").inc()
        return tool_error(f"unsupported intent: {intent}")
    reference_image = None
    if reference_image_index is not None:
        if not user_initiated:
            return tool_error("参考图换房需要用户主动请求")
        if type(reference_image_index) is not int or not 1 <= reference_image_index <= len(user_images):
            return tool_error("找不到指定的参考图，请在当前对话中重新发送图片")
        reference_image = user_images[reference_image_index - 1]
    tier = disturbance_tier or (kwargs.get("user_settings") or {}).get("companion.disturbance_tier")
    if tier is None and user_id is not None:
        tier = await get_disturbance_tier(user_id)
    tier = (tier or "normal").lower()

    if not user_initiated and tier in ("still", "silent"):
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_silent").inc()
        return tool_error("现在我不想动房间的事，等你叫我再说。")
    # 常规档只允许 decorate / mood
    if not user_initiated and tier == "normal" and norm_intent not in _NORMAL_TIER_INTENTS:
        ROOM_BACKDROP_LLM_TRIGGERS_TOTAL.labels(outcome="rejected_tier").inc()
        return tool_error("现在不适宜大改房间，先说点别的吧。")
    try:
        row = await schedule_room_generation(
            user_id,
            origin=BackdropOrigin.USER_REQUEST.value if user_initiated else BackdropOrigin.LLM.value,
            intent=norm_intent,
            notes=notes,
            reference_image=reference_image,
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
    "description": (
        "生成并更换生活空间的房间背景，支持文字描述及场景、姿势参考图，参考图可以包含人物。"
        "画面包含角色本人，保持既定身份与当前穿着。返回后台生成任务的标识与状态，图片异步生成。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["decorate", "seasonal", "mood", "rebuild"],
                "description": "调整类型：decorate=重新布置 / seasonal=换季 / mood=调整氛围 / rebuild=整体重建。默认 decorate。",
            },
            "notes": {
                "type": "string",
                "description": "房间布置、构图与角色姿势的完整要求，例如「参考图中的房间，让角色侧坐在窗边」。角色身份与当前衣着保持不变。",
            },
            "reference_image_index": {
                "type": "integer",
                "minimum": 1,
                "description": "使用参考图时填写当前上下文中最近一条带图用户消息的图片序号，从 1 开始；单图填 1。不使用图片时省略，不填写 URL 或 base64。",
            },
        },
    },
}


def register(registry) -> None:
    REGISTRY.register("room_backdrop_update", ROOM_BACKDROP_UPDATE_SCHEMA, room_backdrop_update_tool)
