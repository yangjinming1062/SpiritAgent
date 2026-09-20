"""逐动作演绎：模型决定角色如何行动，代码固定动作语义与独立短片约束。"""

import json
from typing import Literal

from components import parse_llm_json
from modules.companion import REQUIRED_VIDEO_ACTIONS
from prompts.generation import (
    VIDEO_ACTION_POSE_TEMPLATE,
    VIDEO_ACTION_SCRIPT_INSTRUCTIONS,
    VIDEO_ACTION_SEMANTICS,
    VIDEO_IDLE_MOTION_CONSTRAINTS,
    VIDEO_PROMPT_SKELETON,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from services.infrastructure.llm import chat

VideoAction = Literal["idle", "walk_left", "walk_right", "drag"]
ACTION_DURATIONS: dict[VideoAction, int] = {"idle": 1, "walk_left": 2, "walk_right": 2, "drag": 2}


class VideoScriptError(RuntimeError):
    """脚本不符合动作契约。"""


class ActionScriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: VideoAction
    pose_prompt: str = Field(min_length=10, max_length=400)
    motion_prompt: str = Field(min_length=10, max_length=600)


class ActionScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionScriptEntry]


async def compose_action_script(
    db: AsyncSession | None,
    user_id: int,
    *,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    actions: tuple[str, ...] = REQUIRED_VIDEO_ACTIONS,
    feedback: str = "",
) -> ActionScript:
    payload = {
        "persona": persona_definition,
        "personality_tags": personality_tags,
        "outfit_description": outfit_description,
        "feedback": feedback,
        "actions": [
            {
                "action": action,
                "description": VIDEO_ACTION_SEMANTICS[action],
                "duration_seconds": ACTION_DURATIONS[action],
            }
            for action in actions
        ],
    }
    last_error = "缺少有效动作"
    for _attempt in range(2):
        raw = await chat(db, user_id, VIDEO_ACTION_SCRIPT_INSTRUCTIONS, json.dumps(payload, ensure_ascii=False))
        try:
            script = ActionScript.model_validate(parse_llm_json(raw))
            keys = [entry.action for entry in script.actions]
            if len(keys) != len(actions) or set(keys) != set(actions):
                raise VideoScriptError("动作集合与请求不符")
            script.actions.sort(key=lambda entry: actions.index(entry.action))
            return script
        except (ValidationError, ValueError, VideoScriptError) as exc:
            last_error = str(exc)
    raise VideoScriptError("动作脚本生成失败，请重试") from ValueError(last_error)


def build_video_prompt(entry: ActionScriptEntry) -> str:
    prompt = VIDEO_PROMPT_SKELETON.format(
        action=VIDEO_ACTION_SEMANTICS[entry.action],
        motion=entry.motion_prompt,
        seconds=ACTION_DURATIONS[entry.action],
    )
    if entry.action == "idle":
        prompt += "\n\n" + VIDEO_IDLE_MOTION_CONSTRAINTS
    return prompt


def build_pose_prompt(entry: ActionScriptEntry) -> str:
    return VIDEO_ACTION_POSE_TEMPLATE.format(action=VIDEO_ACTION_SEMANTICS[entry.action], pose=entry.pose_prompt)
