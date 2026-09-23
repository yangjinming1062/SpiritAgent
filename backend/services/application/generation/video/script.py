"""逐动作演绎：模型决定角色如何行动，代码固定动作契约与独立短片约束。

动作规格是开放数据：系统动作沿用固定语义与时长；动态动作由审核通过的提案提供
name / motion_description / duration_seconds / clip_kind。
时长在设计校验、供应商参数与最终产物三处守卫（上限见 domains/actions/policy）。
"""

import json

from components import parse_llm_json
from modules.companion import CharacterCardSnapshot
from prompts.generation import (
    VIDEO_ACTION_POSE_TEMPLATE,
    VIDEO_ACTION_SCRIPT_INSTRUCTIONS,
    VIDEO_IDLE_MOTION_CONSTRAINTS,
    VIDEO_PROMPT_LOOP_CYCLE,
    VIDEO_PROMPT_LOOP_TAIL,
    VIDEO_PROMPT_ONCE_CYCLE,
    VIDEO_PROMPT_SKELETON,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.domains.actions.policy import max_duration_seconds
from services.domains.companion import render_character_identity
from services.infrastructure.llm import vision_chat

# 系统槽位的固定语义；动态动作语义由提案规格携带。
SYSTEM_ACTION_SEMANTICS: dict[str, str] = {
    "idle": "保持参考图中的稳定待机姿态，身体几乎不动，仅有符合实际生理结构的极轻微自然活动",
    "walk_left": "身体侧向画面左侧，已有头部也朝左，原地循环表现向左的自然移动；不是正面对镜头横向跨步，身体中心不平移",
    "walk_right": "身体侧向画面右侧，已有头部也朝右，原地循环表现向右的自然移动；不是正面对镜头横向跨步，身体中心不平移",
    "drag": "身体整体悬空时符合自身结构的轻微摆动，不添加手脚或提拉道具",
}


class VideoScriptError(RuntimeError):
    """脚本不符合动作契约。"""


def _validate_duration(value: float) -> float:
    """时长上限来自后台配置（热更新生效），不能固化为模块常量。"""
    limit = max_duration_seconds()
    if not 0 < value <= limit:
        raise ValueError(f"时长须在 (0, {limit:g}] 秒内")
    return value


class ActionSpec(BaseModel):
    """开放动作规格：一次演绎的目标。系统动作 slot 非空；动态动作 slot 为空。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=32)
    system_slot: str = ""
    name: str = Field(default="", max_length=64)
    semantics: str = Field(default="", max_length=600)
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    feedback: str = ""
    duration_seconds: float
    clip_kind: str = Field(pattern="^(loop|once)$")

    _spec_duration = field_validator("duration_seconds")(_validate_duration)

    def semantics_or(self) -> str:
        return self.semantics or SYSTEM_ACTION_SEMANTICS.get(self.system_slot or self.action, self.name)


class ActionScriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    pose_prompt: str = Field(min_length=10, max_length=400)
    motion_prompt: str = Field(min_length=10, max_length=600)
    # 时长与 clip_kind 由请求规格回填；模型只输出动作键与两段描述。
    duration_seconds: float = 0
    clip_kind: str = ""

    def with_spec(self, spec: ActionSpec) -> "ActionScriptEntry":
        return self.model_copy(
            update={
                "duration_seconds": _validate_duration(spec.duration_seconds),
                "clip_kind": spec.clip_kind,
            },
        )


class ActionScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionScriptEntry]


async def compose_action_script(
    user_id: int,
    *,
    reference_image: str,
    identity: CharacterCardSnapshot,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    specs: list[ActionSpec],
    feedback: str = "",
) -> ActionScript:
    """为一批动作规格编写演绎脚本。返回条目与请求规格一一对应。"""
    payload = {
        "persona": persona_definition,
        "personality_tags": personality_tags,
        "outfit_description": outfit_description,
        "feedback": feedback,
        "actions": [
            {
                "action": spec.action,
                "name": spec.name,
                "system_slot": spec.system_slot,
                "description": spec.semantics_or(),
                "use_when": spec.use_when,
                "avoid_when": spec.avoid_when,
                "feedback": spec.feedback,
                "duration_seconds": spec.duration_seconds,
                "clip_kind": spec.clip_kind,
            }
            for spec in specs
        ],
    }
    last_error = "缺少有效动作"
    for _attempt in range(2):
        raw = await vision_chat(
            user_id,
            VIDEO_ACTION_SCRIPT_INSTRUCTIONS + "\n" + render_character_identity(identity),
            json.dumps(payload, ensure_ascii=False),
            reference_images=(reference_image,),
        )
        try:
            script = ActionScript.model_validate(parse_llm_json(raw))
            keys = [entry.action for entry in script.actions]
            wanted = [spec.action for spec in specs]
            if len(keys) != len(specs) or set(keys) != set(wanted):
                raise VideoScriptError("动作集合与请求不符")
            order = {key: index for index, key in enumerate(wanted)}
            script.actions.sort(key=lambda entry: order[entry.action])
            # 脚本沿用请求规格的时长与模式：不把 once 舞蹈写成短循环。
            script.actions = [entry.with_spec(spec) for entry, spec in zip(script.actions, specs, strict=True)]
            return script
        except (ValidationError, ValueError, VideoScriptError) as exc:
            last_error = str(exc)
            payload["validation_error"] = last_error
    raise VideoScriptError("动作脚本生成失败，请重试") from ValueError(last_error)


def build_video_prompt(entry: ActionScriptEntry, identity: CharacterCardSnapshot) -> str:
    is_loop = entry.clip_kind == "loop"
    prompt = VIDEO_PROMPT_SKELETON.format(
        motion=entry.motion_prompt,
        seconds=entry.duration_seconds,
        tail_clause=VIDEO_PROMPT_LOOP_TAIL if is_loop else "",
        cycle_clause=VIDEO_PROMPT_LOOP_CYCLE if is_loop else VIDEO_PROMPT_ONCE_CYCLE,
    )
    if entry.action == "idle":
        prompt += "\n\n" + VIDEO_IDLE_MOTION_CONSTRAINTS
    return prompt + "\n" + render_character_identity(identity)


def build_pose_prompt(entry: ActionScriptEntry, identity: CharacterCardSnapshot) -> str:
    return (
        VIDEO_ACTION_POSE_TEMPLATE.format(action=entry.motion_prompt, pose=entry.pose_prompt)
        + "\n"
        + render_character_identity(identity)
    )
