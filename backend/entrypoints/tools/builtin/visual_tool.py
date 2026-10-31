"""伙伴影像工具：查询成品、查看制作状态、提交组合制作与请求呈现。"""

import json
from typing import Any

from features.appearance.queries import ready_outfit_snapshots
from features.visuals.queries import list_jobs, list_visuals, visual_detail
from features.visuals.repository import VisualNotFoundError
from infrastructure.api_ids import ResourceId
from infrastructure.database import SESSION_LOCAL
from runtime.tools.registry import ToolsRegistry
from workflows.visuals_companion import generate_visual_reference, play_visual_reference

_MAX_RESULTS = 20

VISUAL_SEARCH_TOOL_DESCRIPTION = (
    "检索已生成的图片或视频，按名称或画面描述筛选。返回 visual_id、媒体类型、画面说明及所用穿着、环境和活动信息；"
    "尚未完成的制作进度通过 visual_inspect 查询。"
)
VISUAL_INSPECT_TOOL_DESCRIPTION = (
    "查看已生成图片或视频的详情，或查询制作进度。传 visual_id 返回画面内容、所用穿着、环境与活动、尺寸、时长及相关制作记录；"
    f"不传 visual_id 时返回最近 {_MAX_RESULTS} 次制作，可用 job_id 从中选择一次。"
)
VISUAL_GENERATE_TOOL_DESCRIPTION = (
    "提交一次制作：指定一套造型、一个环境和一段动作，由你决定具体组合并生成图片或视频。"
    "需要可见运动过程时才选 video，单张画面足够时选 image；制作视频时先产出合格的起始图（独立入库）再生成视频。"
    "没有已生成的图片或视频也能开始第一次制作；制作为付费操作，受理后异步执行，用 visual_inspect 查看状态。"
)
VISUAL_PLAY_TOOL_DESCRIPTION = (
    "请求显示一张已生成的图片，或播放一段已生成的视频。图片保持显示，视频按预设方式循环或播放一次。"
    "返回 queued 表示已安排播放，尚不能确认已经显示；桌面离线时不会播放。"
)


async def visual_search_tool(
    user_id: ResourceId,
    query: str = "",
    media_kind: str | None = None,
    limit: int = 10,
    **_: Any,
) -> str:
    if media_kind not in (None, "image", "video"):
        return json.dumps({"error": "媒体类型须为 image 或 video"}, ensure_ascii=False)
    result = await list_visuals(user_id, query=query, media_kind=media_kind, limit=max(1, min(limit, _MAX_RESULTS)))
    return result.model_dump_json()


async def outfit_list_tool(user_id: ResourceId, query: str = "", **_: Any) -> str:
    async with SESSION_LOCAL() as db:
        items = await ready_outfit_snapshots(db, user_id)
    needle = query.strip().casefold()
    return json.dumps(
        [
            {"id": str(item.id), "name": item.name, "description": item.description}
            for item in items
            if not needle or needle in f"{item.name}\n{item.description}".casefold()
        ],
        ensure_ascii=False,
    )


async def visual_inspect_tool(
    user_id: ResourceId,
    visual_id: ResourceId | None = None,
    job_id: ResourceId | None = None,
    **_: Any,
) -> str:
    if visual_id is not None:
        try:
            detail = await visual_detail(user_id, visual_id)
        except VisualNotFoundError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return detail.model_dump_json()
    result = await list_jobs(user_id, limit=_MAX_RESULTS)
    matched = [job.model_dump(mode="json") for job in result.jobs if job_id is None or job.id == job_id]
    if job_id is not None and not matched:
        return json.dumps({"error": "未找到这次制作"}, ensure_ascii=False)
    return json.dumps(matched, ensure_ascii=False)


async def visual_generate_tool(
    outfit_id: ResourceId,
    scene_id: ResourceId,
    action_id: ResourceId,
    user_id: ResourceId,
    tool_call_id: str,
    parent_session_id: str,
    narrative: str,
    media_kind: str = "image",
    requirements: str = "",
    feedback: str = "",
    video_feedback: str = "",
    **_: Any,
) -> str:
    result = await generate_visual_reference(
        user_id,
        narrative=narrative,
        outfit_id=outfit_id,
        scene_id=scene_id,
        action_id=action_id,
        media_kind="video" if media_kind == "video" else "image",
        requirements=requirements,
        feedback=feedback,
        video_feedback=video_feedback,
        request_id=f"tool:{parent_session_id}:{tool_call_id}",
    )
    return json.dumps(
        {
            key: str(value) if key in {"job_id", "visual_id"} and value is not None else value
            for key, value in result.items()
        },
        ensure_ascii=False,
    )


async def visual_play_tool(
    visual_id: ResourceId,
    user_id: ResourceId,
    proactive_turn: bool = False,
    **_: Any,
) -> str:
    result = await play_visual_reference(user_id, visual_id, proactive_turn=proactive_turn)
    return json.dumps(
        {
            key: str(value) if key in {"job_id", "visual_id"} and value is not None else value
            for key, value in result.items()
        },
        ensure_ascii=False,
    )


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
                    "description": "只看图片或视频；省略返回全部。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_RESULTS, "default": 10},
            },
            [],
        ),
        (
            "visual_inspect",
            visual_inspect_tool,
            {
                "visual_id": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": "已生成图片或视频的 visual_id。",
                },
                "job_id": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": f"某次制作的 id；不传 visual_id 时从最近 {_MAX_RESULTS} 次制作中选择它。",
                },
            },
            [],
        ),
        (
            "visual_generate",
            visual_generate_tool,
            {
                "outfit_id": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": "造型的 outfit_id；一套造型包含服装、发型、妆容与配饰。",
                },
                "scene_id": {"type": "string", "pattern": "^[1-9][0-9]*$", "description": "环境图片的 scene_id。"},
                "action_id": {"type": "string", "pattern": "^[1-9][0-9]*$", "description": "动作的 action_id。"},
                "narrative": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1000,
                    "description": "伙伴在生活中对用户表达的第一人称创作计划和具体缘由，承接角色与当前交流，不预写成品完成。",
                },
                "media_kind": {
                    "type": "string",
                    "enum": ["image", "video"],
                    "default": "image",
                    "description": "制作图片或视频；需要可见运动过程时才选 video，视频会先产出合格起始图。",
                },
                "requirements": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "具体活动、必要画面内容和用途；省略时根据所选穿着、环境和活动安排细节。",
                },
                "feedback": {"type": "string", "maxLength": 2000, "description": "通用定制要求。"},
                "video_feedback": {"type": "string", "maxLength": 2000, "description": "视频定制要求。"},
            },
            ["outfit_id", "scene_id", "action_id", "narrative"],
        ),
        (
            "visual_play",
            visual_play_tool,
            {
                "visual_id": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*$",
                    "description": "要显示的图片或播放的视频的 visual_id。",
                },
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
