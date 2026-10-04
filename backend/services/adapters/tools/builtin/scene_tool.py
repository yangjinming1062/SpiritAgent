"""场景发现、复用与创建；聊天工具只走自主路径，用户手动创建与切换走场景页面。"""

import json
from typing import Any

from components import SCENE_LLM_TRIGGERS_TOTAL, SESSION_LOCAL, tool_error
from modules.companion import CompanionScene, SceneOrigin
from prompts.tools import SCENE_TOOL_DESCRIPTIONS

from services.application.generation import SceneError, activate_scene, schedule_scene_generation
from services.contracts import SceneTurnState
from services.domains.companion import get_scene, get_scene_state, list_scenes, scene_environment
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
    user_id: int,
    scene_turn: SceneTurnState,
    query: str = "",
    offset: int = 0,
    limit: int = 30,
    **_: Any,
) -> str:
    async with SESSION_LOCAL() as db:
        rows, total = await list_scenes(db, user_id, query=query, offset=offset, limit=limit, ready_only=True)
    scene_turn.inspected = True
    return await _result(user_id, scenes=[_asset(row) for row in rows], total=total, offset=max(0, offset))


async def scene_get_tool(scene_id: int, user_id: int, **_: Any) -> str:
    async with SESSION_LOCAL() as db:
        row = await get_scene(db, user_id, scene_id)
    if row is None:
        return tool_error("找不到对应场景")
    return await _result(user_id, scene=_asset(row))


async def scene_create_tool(
    notes: str,
    user_id: int,
    scene_turn: SceneTurnState,
    auto_activate: bool = False,
    **_: Any,
) -> str:
    async with scene_turn.lock:
        if not scene_turn.inspected:
            return tool_error("请先用 scene_list 检查已有场景，适合时直接启用")
        if scene_turn.create_claimed:
            return tool_error("本回合已提交一次场景创建，请查询结果，不要重复创建")
        if auto_activate and scene_turn.switch_claimed:
            return tool_error("本回合已提交一次环境切换，不能再申请自动启用")
        if not notes.strip():
            return tool_error("请提供具体的场景设计")
        try:
            row = await schedule_scene_generation(
                user_id,
                origin=SceneOrigin.LLM.value,
                notes=notes,
                auto_activate=auto_activate,
            )
        except SceneError as exc:
            SCENE_LLM_TRIGGERS_TOTAL.labels(outcome="rejected").inc()
            return tool_error(str(exc))
        scene_turn.create_claimed = True
        if auto_activate:
            scene_turn.switch_claimed = True
        SCENE_LLM_TRIGGERS_TOTAL.labels(outcome="accepted").inc()
        return await _result(
            user_id,
            accepted=True,
            scene=_asset(row),
            auto_activate_requested=auto_activate,
            activated=False,
            message=(
                "场景正在准备，完成后保存到场景库并尝试自动启用；只有 environment.current 更新才表示已经到达"
                if auto_activate
                else "场景正在准备，完成后保存到场景库；当前环境保持不变"
            ),
        )


async def scene_activate_tool(
    scene_id: int,
    user_id: int,
    scene_turn: SceneTurnState,
    **_: Any,
) -> str:
    async with scene_turn.lock:
        if scene_turn.switch_claimed:
            return tool_error("本回合已提交一次环境切换，请查询结果，不要重复切换")
        try:
            async with SESSION_LOCAL() as db:
                row = await activate_scene(db, user_id, scene_id, origin=SceneOrigin.LLM.value)
        except SceneError as exc:
            return tool_error(str(exc))
        scene_turn.switch_claimed = True
        return await _result(user_id, activated=True, scene=_asset(row))


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
                "notes": {
                    "type": "string",
                    "description": "环境壁纸设计，非空：写清地点、陈设、光线、氛围与需要的画风，不描绘你本人；其他人物、动物、肖像或雕像可以按环境需要出现。",
                },
                "auto_activate": {
                    "type": "boolean",
                    "description": (
                        "默认 false，仅创建并保存到场景库，当前环境不变。自主决定在准备完成后切换时传 true，仍须通过政策校验。"
                    ),
                },
            },
            ["notes"],
        ),
        ("scene_activate", scene_activate_tool, {"scene_id": {"type": "integer", "minimum": 1}}, ["scene_id"]),
    ]
    for name, handler, properties, required in definitions:
        registry.register(
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
