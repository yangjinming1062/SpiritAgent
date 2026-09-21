"""场景发现、复用与创建；用户明确请求与自主决定使用不同授权路径。"""

import json
from typing import Any

from components import LLM_MAX_OUTPUT_TOKENS, SCENE_LLM_TRIGGERS_TOTAL, SESSION_LOCAL, parse_llm_json, tool_error
from modules.companion import CompanionScene, SceneOrigin
from prompts.tools import SCENE_REQUEST_CHECK_SYSTEM, SCENE_TOOL_DESCRIPTIONS

from services.application.generation import SceneError, activate_scene, schedule_scene_generation
from services.contracts import SceneTurnState
from services.domains.companion import get_disturbance_tier, get_scene, get_scene_state, list_scenes, scene_environment
from services.infrastructure.llm import call_llm_once
from services.infrastructure.tool_runtime import ToolsRegistry


def _asset(row: CompanionScene) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "stage": row.stage,
        "error": row.error,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
    }


async def _result(user_id: int, **payload: Any) -> str:
    async with SESSION_LOCAL() as db:
        payload["environment"] = scene_environment(await get_scene_state(db, user_id))
    return json.dumps(payload, ensure_ascii=False)


async def scene_list_tool(
    query: str = "",
    offset: int = 0,
    limit: int = 30,
    user_id: int | None = None,
    scene_turn: SceneTurnState | None = None,
    **_: Any,
) -> str:
    if user_id is None:
        return tool_error("缺少用户上下文")
    async with SESSION_LOCAL() as db:
        rows, total = await list_scenes(db, user_id, query=query, offset=offset, limit=limit, ready_only=True)
    if scene_turn:
        scene_turn.inspected = True
    return await _result(user_id, scenes=[_asset(row) for row in rows], total=total, offset=max(0, offset))


async def scene_get_tool(scene_id: int, user_id: int | None = None, **_: Any) -> str:
    if user_id is None:
        return tool_error("缺少用户上下文")
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
    if row is None:
        return tool_error("找不到对应场景")
    return await _result(user_id, scene=_asset(row))


async def _origin(
    user_id: int,
    request_mode: str,
    request_basis: str,
    user_initiated: bool,
    scene_turn: SceneTurnState,
    llm_config: dict[str, Any],
) -> str:
    if request_mode == "user_request":
        quote = request_basis.strip()
        if not user_initiated or not quote or quote not in scene_turn.user_text:
            raise SceneError("用户请求路径需要真实本轮用户明确要求切换或创建场景的原文依据")
        try:
            raw = await call_llm_once(
                llm_config,
                SCENE_REQUEST_CHECK_SYSTEM,
                {"user_message": scene_turn.user_text, "quoted_basis": quote},
                max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
                json_output=True,
            )
        except Exception as exc:
            raise SceneError("暂时无法核对场景请求，请稍后重试；当前环境保持不变") from exc
        parsed = parse_llm_json(raw)
        if not isinstance(parsed, dict) or parsed.get("explicit_scene_request") is not True:
            raise SceneError("当前用户消息未明确要求场景操作；伙伴自主决定应使用 autonomous 并遵守政策与额度")
        return SceneOrigin.USER_REQUEST.value
    if request_mode != "autonomous":
        raise SceneError("request_mode 无效")
    if await get_disturbance_tier(user_id) in {"still", "silent"}:
        raise SceneError("静止档不发起在线自主场景变化")
    return SceneOrigin.LLM.value


async def scene_create_tool(
    notes: str,
    outfit_description: str | None = None,
    request_mode: str = "autonomous",
    request_basis: str = "",
    reference_image_index: int | None = None,
    user_id: int | None = None,
    user_initiated: bool = False,
    user_images: tuple[str, ...] = (),
    scene_turn: SceneTurnState | None = None,
    llm_config: dict[str, Any] | None = None,
    **_: Any,
) -> str:
    if user_id is None or scene_turn is None:
        return tool_error("场景切换需要有效回合上下文")
    async with scene_turn.lock:
        if not scene_turn.inspected:
            return tool_error("请先用 scene_list 检查已有场景，适合时直接启用")
        if scene_turn.switch_claimed:
            return tool_error("本回合已提交一次环境切换，请查询结果，不要重复切换")
        if not notes.strip() or len(notes) > 2000:
            return tool_error("请提供不超过 2000 字符的具体场景设计")
        try:
            origin = await _origin(user_id, request_mode, request_basis, user_initiated, scene_turn, llm_config or {})
            reference = None
            if reference_image_index is not None:
                if (
                    origin != SceneOrigin.USER_REQUEST.value
                    or type(reference_image_index) is not int
                    or not 1 <= reference_image_index <= len(user_images)
                ):
                    return tool_error("场景参考需要用户明确请求和有效的当前附件序号")
                reference = user_images[reference_image_index - 1]
            row = await schedule_scene_generation(
                user_id,
                origin=origin,
                notes=notes,
                outfit_description=outfit_description,
                reference_image=reference,
                auto_activate=True,
            )
        except SceneError as exc:
            SCENE_LLM_TRIGGERS_TOTAL.labels(outcome="rejected").inc()
            return tool_error(str(exc))
        scene_turn.switch_claimed = True
        SCENE_LLM_TRIGGERS_TOTAL.labels(outcome="accepted").inc()
        return await _result(
            user_id,
            accepted=True,
            scene=_asset(row),
            activated=False,
            message="场景正在准备；只有成功启用后才能叙述已经到达",
        )


async def scene_activate_tool(
    scene_id: int,
    request_mode: str = "autonomous",
    request_basis: str = "",
    user_id: int | None = None,
    user_initiated: bool = False,
    scene_turn: SceneTurnState | None = None,
    llm_config: dict[str, Any] | None = None,
    **_: Any,
) -> str:
    if user_id is None or scene_turn is None:
        return tool_error("场景切换需要有效回合上下文")
    async with scene_turn.lock:
        if scene_turn.switch_claimed:
            return tool_error("本回合已提交一次环境切换")
        try:
            origin = await _origin(user_id, request_mode, request_basis, user_initiated, scene_turn, llm_config or {})
            async with SESSION_LOCAL() as db:
                row = await activate_scene(db, user_id, scene_id, origin=origin)
        except SceneError as exc:
            return tool_error(str(exc))
        scene_turn.switch_claimed = True
        return await _result(user_id, activated=True, scene=_asset(row))


_REQUEST_PROPERTIES = {
    "request_mode": {
        "type": "string",
        "enum": ["autonomous", "user_request"],
        "description": "默认 autonomous；真实本轮用户明确要求场景操作才用 user_request，用户发起聊天本身不是授权。",
    },
    "request_basis": {
        "type": "string",
        "description": "user_request 必须引用本轮用户明确要求场景操作的原文；普通旅行讨论、创作或假设不算。",
    },
}


def register(registry: ToolsRegistry) -> None:
    definitions = [
        (
            "scene_list",
            scene_list_tool,
            {
                "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            [],
        ),
        ("scene_get", scene_get_tool, {"scene_id": {"type": "integer", "minimum": 1}}, ["scene_id"]),
        (
            "scene_create",
            scene_create_tool,
            {
                **_REQUEST_PROPERTIES,
                "notes": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "具体的地点、环境、活动与氛围。着装写在 outfit_description 中。",
                },
                "outfit_description": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "本次明确提出的完整着装描述，包括衣物、配色、鞋履和配饰等；未提出着装要求时省略。",
                },
                "reference_image_index": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "可选，仅用于用户明确请求：最近一条带图用户消息中的图片序号，从 1 开始；不使用参考图时省略。",
                },
            },
            ["notes"],
        ),
        (
            "scene_activate",
            scene_activate_tool,
            {**_REQUEST_PROPERTIES, "scene_id": {"type": "integer", "minimum": 1}},
            ["scene_id"],
        ),
    ]
    for name, handler, properties, required in definitions:
        registry.register(
            name,
            {
                "name": name,
                "description": SCENE_TOOL_DESCRIPTIONS[name],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler,
        )
