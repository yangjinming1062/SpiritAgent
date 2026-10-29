"""伙伴动作素材工具：查询与登记动作语义定义，不表示可播影像或自动开始制作。"""

import json
from typing import Any

from features.action_library import queries as action_queries
from features.action_library.schemas import ActionAssetDesign, ActionAssetView, ActionClipKind, ActionTrigger
from features.media.visual_semantics import ActionMotionIntensity, ActionPerformanceRole, SemanticTag
from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from runtime.tools.registry import ToolsRegistry
from workflows.companion_action_prompts import ACTION_DESIGN_MOTION_DESCRIPTION
from workflows.visuals_companion import design_action_reference

_MAX_SEARCH_RESULTS = 20

ACTION_SEARCH_TOOL_DESCRIPTION = (
    "检索已经入库、可以用于制作的动作。返回名称、动作内容、适用条件、持续或短动作类型与时长；"
    "入库不代表已有图片或视频，制作使用 visual_generate，查询已有成品使用 visual_search。"
)
ACTION_INSPECT_TOOL_DESCRIPTION = (
    "查看一个动作的定义、评审状态及原因。approved 表示可用于制作，pending/reviewing 表示等待或正在评审；"
    "deferred 表示待补充资料，rejected 表示内容未通过，failed 表示技术失败、尚未取得评审结论。"
)
ACTION_DESIGN_TOOL_DESCRIPTION = (
    "登记一个新动作（名称、动作内容、结构化语义标签、表现角色、动作强度、适用条件、持续或短动作类型与时长）。"
    "登记只保存定义并进入评审，不开始生成图片或视频；评审依据是角色身体能力与动作含义，具体环境与道具在每次生成时判断。"
    "已有合适的入库动作时优先复用；提交后用返回的 action_id 调用 action_inspect 查看结果，通过后才能用于制作。"
    "不要用重复登记代替评审重试。"
)


def _action_payload(action: ActionAssetView) -> dict[str, Any]:
    return action.model_dump(
        mode="json",
        include={
            "id",
            "name",
            "motion_description",
            "use_when",
            "avoid_when",
            "allowed_triggers",
            "clip_kind",
            "duration_seconds",
            "semantic_tags",
            "performance_role",
            "motion_intensity",
            "interruptible",
            "reason",
            "status",
            "review_reason",
        },
    )


async def action_search_tool(
    user_id: ResourceId,
    query: str = "",
    limit: int = 10,
    cursor: ResourceId | None = None,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        result = await action_queries.list_actions(
            db,
            user_id,
            query=query,
            limit=max(1, min(limit, _MAX_SEARCH_RESULTS)),
            cursor=cursor,
        )
    return json.dumps(
        {
            "actions": [_action_payload(item) for item in result.actions],
            "has_more": result.has_more,
            "next_cursor": str(result.next_cursor) if result.next_cursor is not None else None,
        },
        ensure_ascii=False,
    )


async def action_inspect_tool(user_id: ResourceId, action_id: ResourceId, **_: Any) -> str:
    async with SESSION_LOCAL() as db:
        item = await action_queries.action_detail(db, user_id, action_id)
    if item is None:
        return json.dumps({"error": "未找到该动作"}, ensure_ascii=False)
    return json.dumps(_action_payload(item), ensure_ascii=False)


async def action_design_tool(
    name: str,
    motion_description: str,
    user_id: ResourceId,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    allowed_triggers: list[ActionTrigger] | None = None,
    semantic_tags: list[SemanticTag] | None = None,
    performance_role: ActionPerformanceRole | None = None,
    motion_intensity: ActionMotionIntensity = "normal",
    interruptible: bool = True,
    reason: str = "",
    duration_seconds: int = 4,
    clip_kind: ActionClipKind = "once",
    **_: Any,
) -> str:
    result = await design_action_reference(
        user_id,
        name=name,
        motion_description=motion_description,
        use_when=use_when,
        avoid_when=avoid_when,
        allowed_triggers=allowed_triggers,
        semantic_tags=semantic_tags,
        performance_role=performance_role,
        motion_intensity=motion_intensity,
        interruptible=interruptible,
        reason=reason,
        duration_seconds=duration_seconds,
        clip_kind=clip_kind,
    )
    return json.dumps({**result, "action_id": str(result["action_id"])}, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    design_schema = ActionAssetDesign.model_json_schema()
    design_schema.pop("title", None)
    design_descriptions = {
        "name": "用户可见的简短动作名称，使用当前对话的语言。",
        "motion_description": ACTION_DESIGN_MOTION_DESCRIPTION,
        "use_when": "适用条件列表，如「用户主动请求拥抱」。",
        "avoid_when": "应避免使用的条件。",
        "allowed_triggers": (
            "允许发起动作的情境：chat 为对话回应，manual 为用户点播，periodic 为自主日常活动，"
            "user_return 为用户回来；省略或空列表使用 chat 与 manual。"
        ),
        "reason": "为何需要新动作；已有合适的入库动作时先复用。",
        "duration_seconds": "预计时长（整秒），按一次可见表达与必要停留填写。",
        "clip_kind": (
            "loop 持续保持状态或自然连续的运动，首尾姿态、接触及道具位置和可见状态均须相接；"
            "不能自然闭合的过程使用 once，完成一次表达并自然收束。"
        ),
        "semantic_tags": (
            "动作的结构化语义标签，只能从这些值选择：idle 安静相伴、attention 注意用户、interaction 互动回应、"
            "reading 阅读、working 专注做事、creating 创作、thinking 思考、resting 休息、organizing 整理、"
            "socializing 社交、stretching 舒展、grooming 仪容整理、drinking 饮水、dancing 舞蹈。"
            "描述动作本身而非使用场景；场景归 use_when。"
        ),
        "performance_role": (
            "表现角色：idle 常驻待机、sustained 可持续的专注状态、reaction 一次性行为反应；"
            "省略时 loop 视为 sustained，once 视为 reaction。"
        ),
        "motion_intensity": (
            "动作幅度强度：subtle 轻微到几乎不可见、normal 日常幅度、expressive 明显大幅度；"
            "轻量动态档只自动选择 subtle 动作，用户明确触发的反应可使用其他强度。"
        ),
        "interruptible": "是否允许已开始的动作被更高优先级互动或新的生活情境中断；结束后恢复当前有效的基础画面。",
    }
    for name, description in design_descriptions.items():
        design_schema["properties"][name]["description"] = description
    definitions = [
        (
            "action_search",
            action_search_tool,
            {
                "type": "object",
                "properties": {
                    "cursor": {
                        "type": "string",
                        "pattern": "^[1-9][0-9]*$",
                        "description": "继续读取时使用上次返回的 next_cursor；首次查询省略。",
                    },
                    "query": {
                        "type": "string",
                        "description": "按名称或动作内容匹配完整关键词；留空不筛选。返回数量仍受 limit 限制。",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_SEARCH_RESULTS, "default": 10},
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        (
            "action_inspect",
            action_inspect_tool,
            {
                "type": "object",
                "properties": {
                    "action_id": {
                        "type": "string",
                        "pattern": "^[1-9][0-9]*$",
                        "description": "动作标识，来自 action_search 或 action_design。",
                    },
                },
                "required": ["action_id"],
                "additionalProperties": False,
            },
        ),
        ("action_design", action_design_tool, design_schema),
    ]
    descriptions = {
        "action_search": ACTION_SEARCH_TOOL_DESCRIPTION,
        "action_design": ACTION_DESIGN_TOOL_DESCRIPTION,
        "action_inspect": ACTION_INSPECT_TOOL_DESCRIPTION,
    }
    for name, handler, parameters in definitions:
        registry.register({"name": name, "description": descriptions[name], "parameters": parameters}, handler)
