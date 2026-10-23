"""伙伴影像工具：查询成品、查看制作状态、提交组合制作与请求呈现。"""

import json
from typing import Any

from features.appearance.queries import ready_outfit_snapshots
from features.visuals.queries import list_jobs, list_visuals, visual_detail
from features.visuals.repository import VisualNotFoundError
from infrastructure.database import SESSION_LOCAL
from runtime.tools.registry import ToolsRegistry
from workflows.visuals_companion import generate_visual_reference, play_visual_reference

VISUAL_SEARCH_TOOL_DESCRIPTION = (
    "检索已生成的图片或视频成品。每份成品由一套造型、一个环境和一段动作组合生成，"
    "按名称或来源筛选；列表含进行中的制作状态。"
)
VISUAL_INSPECT_TOOL_DESCRIPTION = (
    "查看成品或制作工作项：传 visual_id 返回画面描述、来源（造型、环境与动作）、尺寸与时长等完整详情；"
    "不传 visual_id 时按 job_id 列出相关制作状态。"
)
VISUAL_GENERATE_TOOL_DESCRIPTION = (
    "提交一次制作：指定一套造型、一个环境和一段动作，由你决定具体组合并生成图片或视频。"
    "需要可见运动过程时才选 video，单张画面足够时选 image；制作视频时先产出合格的起始图（独立入库）再生成视频。"
    "不需要已有成品即可开始第一次制作；制作为付费操作，受理后异步执行，用 visual_inspect 查看状态。"
)
VISUAL_PLAY_TOOL_DESCRIPTION = (
    "请求呈现一份已生成的成品：图片作为持续画面，视频按动作类型循环或作为短动作播放。"
    "受理只表示指令已登记，实际播放以桌面客户端回执为准；桌面离线时不会播放。"
)


async def visual_search_tool(
    user_id: int,
    query: str = "",
    media_kind: str | None = None,
    limit: int = 10,
    **_: Any,
) -> str:
    if media_kind not in (None, "image", "video"):
        return json.dumps({"error": "媒体类型须为 image 或 video"}, ensure_ascii=False)
    result = await list_visuals(user_id, query=query, media_kind=media_kind, limit=max(1, min(limit, 20)))
    return result.model_dump_json()


async def outfit_list_tool(user_id: int, query: str = "", **_: Any) -> str:
    async with SESSION_LOCAL() as db:
        items = await ready_outfit_snapshots(db, user_id)
    needle = query.strip().casefold()
    return json.dumps(
        [
            {"id": item.id, "name": item.name, "description": item.description}
            for item in items
            if not needle or needle in f"{item.name}\n{item.description}".casefold()
        ],
        ensure_ascii=False,
    )


async def visual_inspect_tool(
    user_id: int,
    visual_id: int | None = None,
    job_id: int | None = None,
    **_: Any,
) -> str:
    if visual_id is not None:
        try:
            detail = await visual_detail(user_id, visual_id)
        except VisualNotFoundError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return detail.model_dump_json()
    result = await list_jobs(user_id, limit=20)
    matched = [job.model_dump(mode="json") for job in result.jobs if job_id is None or job.id == job_id]
    if job_id is not None and not matched:
        return json.dumps({"error": "未找到该制作工作项"}, ensure_ascii=False)
    return json.dumps(matched, ensure_ascii=False)


async def visual_generate_tool(
    outfit_id: int,
    scene_id: int,
    action_id: int,
    user_id: int,
    media_kind: str = "image",
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    **_: Any,
) -> str:
    result = await generate_visual_reference(
        user_id,
        outfit_id=outfit_id,
        scene_id=scene_id,
        action_id=action_id,
        media_kind="video" if media_kind == "video" else "image",
        requirements=requirements,
        feedback=feedback,
        video_feedback=video_feedback,
    )
    return json.dumps(result, ensure_ascii=False)


async def visual_play_tool(
    visual_id: int,
    user_id: int,
    reason: str = "",
    proactive_turn: bool = False,
    **_: Any,
) -> str:
    result = await play_visual_reference(user_id, visual_id, reason=reason, proactive_turn=proactive_turn)
    return json.dumps(result, ensure_ascii=False)


def register(registry: ToolsRegistry) -> None:
    definitions = [
        (
            "outfit_list",
            outfit_list_tool,
            {"query": {"type": "string", "description": "按造型名称或描述筛选，留空返回全部。"}},
            [],
        ),
        (
            "visual_search",
            visual_search_tool,
            {
                "query": {"type": "string", "description": "按名称或画面描述文本匹配；留空不筛选。"},
                "media_kind": {
                    "type": "string",
                    "enum": ["image", "video"],
                    "description": "只看图片或视频成品；省略返回全部。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
            },
            [],
        ),
        (
            "visual_inspect",
            visual_inspect_tool,
            {
                "visual_id": {"type": "integer", "minimum": 1, "description": "成品的 visual_id。"},
                "job_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "制作工作项标识；不传 visual_id 时列出相关制作状态。",
                },
            },
            [],
        ),
        (
            "visual_generate",
            visual_generate_tool,
            {
                "outfit_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "造型的 outfit_id；一套造型包含服装、发型、妆容与配饰。",
                },
                "scene_id": {"type": "integer", "minimum": 1, "description": "环境图片的 scene_id。"},
                "action_id": {"type": "integer", "minimum": 1, "description": "动作的 action_id。"},
                "media_kind": {
                    "type": "string",
                    "enum": ["image", "video"],
                    "default": "image",
                    "description": "制作图片或视频；需要可见运动过程时才选 video，视频会先产出合格起始图。",
                },
                "requirements": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "本次制作的要求；省略时按造型、环境与动作自由组合。",
                },
                "feedback": {"type": "string", "maxLength": 2000, "description": "通用定制要求。"},
                "video_feedback": {"type": "string", "maxLength": 2000, "description": "视频定制要求。"},
            },
            ["outfit_id", "scene_id", "action_id"],
        ),
        (
            "visual_play",
            visual_play_tool,
            {
                "visual_id": {"type": "integer", "minimum": 1, "description": "要呈现的成品 visual_id。"},
                "reason": {"type": "string", "maxLength": 200, "description": "本次呈现的简短情境依据。"},
            },
            ["visual_id"],
        ),
    ]
    descriptions = {
        "outfit_list": "查询已就绪的造型（服装、发型、妆容与配饰），返回供 visual_generate 使用的 outfit_id、名称与描述；查询不改变呈现。",
        "visual_search": VISUAL_SEARCH_TOOL_DESCRIPTION,
        "visual_inspect": VISUAL_INSPECT_TOOL_DESCRIPTION,
        "visual_generate": VISUAL_GENERATE_TOOL_DESCRIPTION,
        "visual_play": VISUAL_PLAY_TOOL_DESCRIPTION,
    }
    for name, handler, properties, required in definitions:
        registry.register(
            {
                "name": name,
                "description": descriptions[name],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
            handler,
        )
