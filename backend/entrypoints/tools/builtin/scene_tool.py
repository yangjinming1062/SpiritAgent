"""环境素材发现、复用与创建；聊天工具只走自主添置路径。"""

import json
from typing import Any

from features.scenes.models import SceneOrigin
from features.scenes.queries import scene_policy, scene_snapshot
from features.scenes.query_contracts import SceneSnapshot
from features.scenes.service import SceneError
from features.scenes.snapshots import list_scene_snapshots, schedule_scene_snapshot
from features.scenes.turn_state import SceneTurnState
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import tool_error
from runtime.tools.prompts import SCENE_TOOL_DESCRIPTIONS
from runtime.tools.registry import ToolsRegistry


def _asset(row: SceneSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "stage": row.stage,
        "error": row.error,
    }


def _result(**payload: Any) -> str:
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
        rows, total = await list_scene_snapshots(db, user_id, query=query, offset=offset, limit=limit, ready_only=True)
        policy = await scene_policy(db, user_id)
    scene_turn.inspected = True
    return _result(scenes=[_asset(row) for row in rows], total=total, offset=max(0, offset), policy=policy.value)


async def scene_get_tool(scene_id: int, user_id: int, **_: Any) -> str:
    async with SESSION_LOCAL() as db:
        row = await scene_snapshot(db, user_id, scene_id)
    if row is None:
        return tool_error("找不到对应的环境图片")
    return _result(scene=_asset(row))


async def scene_create_tool(
    notes: str,
    user_id: int,
    scene_turn: SceneTurnState,
    **_: Any,
) -> str:
    async with scene_turn.lock:
        if not scene_turn.inspected:
            return tool_error("请先用 scene_list 查看已有环境图片，合适就复用")
        if scene_turn.create_claimed:
            return tool_error("本回合已创建过一张环境图片，请查询结果，不要重复创建")
        if not notes.strip():
            return tool_error("请提供具体的环境设计")
        try:
            row = await schedule_scene_snapshot(
                user_id,
                origin=SceneOrigin.LLM.value,
                notes=notes,
            )
        except SceneError as exc:
            return tool_error(str(exc))
        scene_turn.create_claimed = True
        return _result(
            accepted=True,
            scene=_asset(row),
            message="环境图片正在准备，完成后会保存到列表中",
        )


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
                    "description": "环境场景设计，非空：写清地点、陈设、光线与氛围，不写画风，不描绘你本人；其他人物、动物、肖像或雕像可以按环境需要出现。",
                },
            },
            ["notes"],
        ),
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
