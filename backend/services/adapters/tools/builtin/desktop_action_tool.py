"""桌面生活动作工具；模式、来源与播放资格由服务端确认。"""

import json
from typing import Any

from components import SESSION_LOCAL, tool_error
from modules.companion import DesktopVideoDesignRequest, DesktopVideoPlayRequest
from prompts.desktop_tools import DESKTOP_ACTION_PARAMETER_DESCRIPTIONS, DESKTOP_ACTION_TOOL_DESCRIPTIONS
from pydantic import ValidationError

from services.application.actions import (
    DesktopVideoError,
    design_desktop_action,
    get_desktop_action_response,
    get_desktop_proposal_response,
    get_desktop_video_state,
    play_desktop_action,
)
from services.domains.companion import get_presentation_snapshot
from services.infrastructure.tool_runtime import ToolsRegistry


def _is_desktop(user_id: int) -> bool:
    presentation = get_presentation_snapshot(user_id)
    return presentation is not None and presentation.mode == "desktop"


def _parameter_error(exc: ValidationError) -> str:
    fields = sorted(
        {str(error["loc"][0]) for error in exc.errors(include_input=False, include_url=False) if error["loc"]},
    )
    return tool_error("桌面动作参数不合法，请检查：" + "、".join(fields))


async def desktop_action_search_tool(user_id: int, query: str = "", limit: int = 10, **_: Any) -> str:
    if not _is_desktop(user_id):
        return tool_error("当前不在桌面模式，请使用当前模式的动作工具")
    async with SESSION_LOCAL() as db:
        state = await get_desktop_video_state(db, user_id)
    if state.current is None:
        return tool_error("当前桌面生活画面尚未准备")
    needle = query.strip().casefold()
    hits = [
        action.model_dump(
            mode="json",
            exclude={"poster_url", "video_url", "candidate_video_url", "candidate_poster_url"},
        )
        for action in state.current.actions
        if action.enabled and action.video_url and needle in f"{action.name}\n{action.description}".casefold()
    ]
    return json.dumps(
        {
            "set_id": state.current.id,
            "hits": hits[: max(1, min(limit, 20))],
            "total": len(hits),
            "pinned": state.pinned,
            "autonomous_enabled": state.autonomous_enabled,
        },
        ensure_ascii=False,
    )


async def desktop_action_design_tool(
    user_id: int,
    name: str,
    motion_description: str,
    kind: str = "once",
    duration_seconds: int = 6,
    use_when: list[str] | None = None,
    avoid_when: list[str] | None = None,
    reason: str = "",
    expected_set_id: int | None = None,
    proactive_turn: bool = False,
    **_: Any,
) -> str:
    if not _is_desktop(user_id):
        return tool_error("当前不在桌面模式，请使用当前模式的动作工具")
    try:
        request = DesktopVideoDesignRequest(
            name=name,
            motion_description=motion_description,
            kind=kind,
            duration_seconds=duration_seconds,
            use_when=use_when or [],
            avoid_when=avoid_when or [],
            reason=reason,
            expected_set_id=expected_set_id,
        )
        result = await design_desktop_action(
            user_id,
            request,
            source="autonomous" if proactive_turn else "user_requested",
            require_desktop=True,
        )
    except ValidationError as exc:
        return _parameter_error(exc)
    except DesktopVideoError as exc:
        return tool_error(str(exc))
    return result.model_dump_json()


async def desktop_action_inspect_tool(
    user_id: int,
    proposal_id: int | None = None,
    action_id: int | None = None,
    **_: Any,
) -> str:
    if not _is_desktop(user_id):
        return tool_error("当前不在桌面模式，请使用当前模式的动作工具")
    if (proposal_id is None) == (action_id is None):
        return tool_error("请仅指定 proposal_id 或 action_id 其中一个")
    try:
        async with SESSION_LOCAL() as db:
            state = await get_desktop_video_state(db, user_id)
            result = (
                await get_desktop_proposal_response(db, user_id, proposal_id)
                if proposal_id is not None
                else await get_desktop_action_response(db, user_id, action_id)
            )
            if state.current is None or result.set_id != state.current.id:
                return tool_error("动作或提案不属于当前桌面生活画面")
    except DesktopVideoError as exc:
        return tool_error(str(exc))
    return result.model_dump_json(exclude={"poster_url", "video_url", "candidate_video_url", "candidate_poster_url"})


async def desktop_action_play_tool(
    user_id: int,
    action_id: int,
    reason: str = "",
    expected_set_id: int | None = None,
    proactive_turn: bool = False,
    **_: Any,
) -> str:
    if not _is_desktop(user_id):
        return tool_error("当前不在桌面模式，请使用当前模式的动作工具")
    try:
        request = DesktopVideoPlayRequest(action_id=action_id, expected_set_id=expected_set_id, reason=reason)
        async with SESSION_LOCAL() as db:
            command = await play_desktop_action(
                db,
                user_id,
                request,
                source="autonomous" if proactive_turn else "chat_expression",
            )
            await db.commit()
    except ValidationError as exc:
        return _parameter_error(exc)
    except DesktopVideoError as exc:
        return tool_error(str(exc))
    return json.dumps(
        {
            "outcome": "queued",
            "play_id": command.play_id,
            "action_id": command.action_id,
            "set_id": command.set_id,
            "played": False,
        },
        ensure_ascii=False,
    )


def register(registry: ToolsRegistry) -> None:
    design_schema = DesktopVideoDesignRequest.model_json_schema()
    design_schema["required"] = ["name", "motion_description", "expected_set_id"]
    play_schema = DesktopVideoPlayRequest.model_json_schema()
    play_schema["required"] = ["action_id", "expected_set_id"]
    definitions = [
        (
            "desktop_action_search",
            desktop_action_search_tool,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                },
                "required": [],
            },
        ),
        ("desktop_action_design", desktop_action_design_tool, design_schema),
        (
            "desktop_action_inspect",
            desktop_action_inspect_tool,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "proposal_id": {"type": "integer", "minimum": 1},
                    "action_id": {"type": "integer", "minimum": 1},
                },
                "required": [],
            },
        ),
        ("desktop_action_play", desktop_action_play_tool, play_schema),
    ]
    for name, handler, parameters in definitions:
        for field, schema in parameters["properties"].items():
            schema["description"] = DESKTOP_ACTION_PARAMETER_DESCRIPTIONS[field]
        registry.register(
            {"name": name, "description": DESKTOP_ACTION_TOOL_DESCRIPTIONS[name], "parameters": parameters},
            handler,
        )
