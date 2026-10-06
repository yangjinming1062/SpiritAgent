"""动作脚本与探身定位：模型描述画面，代码绑定规格并校验结果。"""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from components import get_logger, parse_llm_json
from modules.companion import ABSOLUTE_MAX_DURATION_SECONDS, CharacterCardSnapshot, PeekGeometry
from prompts.generation import (
    ACTION_IMAGE_DESCRIPTION_INSTRUCTIONS,
    ACTION_IMAGE_TEMPLATE,
    PEEK_ACTION_POSE_DESCRIPTION,
    PEEK_GEOMETRY_INSTRUCTIONS,
    VIDEO_ACTION_POSE_TEMPLATE,
    VIDEO_ACTION_SCRIPT_INSTRUCTIONS,
    VIDEO_PROMPT_LOOP_CYCLE,
    VIDEO_PROMPT_LOOP_TAIL,
    VIDEO_PROMPT_ONCE_CYCLE,
    VIDEO_PROMPT_SKELETON,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from services.domains.companion import (
    render_character_identity,
    render_character_profile,
    render_character_video_identity,
)
from services.infrastructure.llm import LlmCallBlockedError, vision_chat
from services.infrastructure.video_processing import ACTION_FRAME_MARGIN

logger = get_logger(__name__)

# 系统槽位的固定语义；动态动作语义由提案规格携带。
SYSTEM_ACTION_SEMANTICS: dict[str, str] = {
    "idle": (
        "沿用参考图中的基本待机姿态，安定地陪伴观看者。"
        "按角色已有的身体结构自然呼吸，胸腹或对应部位轻微起伏。"
        "有可眨动的眼睑时自然眨眼，每秒完成 1-2 次完整开合，眨眼自然流畅，节奏放松。"
        "身体朝向与重心保持稳定，细微活动贯穿待机过程。"
    ),
    "walk_left": (
        "身体及已有头部持续侧向画面左侧，以符合实际结构的步态、游动、蠕动或振翅原地循环表现向左移动；"
        "躯干中心不平移，不正对镜头横向跨步，不转身或回头"
    ),
    "walk_right": (
        "身体及已有头部持续侧向画面右侧，以符合实际结构的步态、游动、蠕动或振翅原地循环表现向右移动；"
        "躯干中心不平移，不正对镜头横向跨步，不转身或回头"
    ),
    "peek_left": PEEK_ACTION_POSE_DESCRIPTION.format(direction="左", opposite="右"),
    "peek_right": PEEK_ACTION_POSE_DESCRIPTION.format(direction="右", opposite="左"),
    "drag": (
        "呈被上方无形力量轻轻拎起的松弛悬垂姿态，提拉处在躯干上部，其余身体受重力向下垂落，身体与衣物均离地。"
        "按已有结构表现受力：有肩背时肩部略提、躯干微前倾；有手臂时双臂沿体侧松垂，肘腕放松；"
        "有腿脚时双腿不承重，膝盖自然弯曲，小腿稍向后垂、脚尖朝下，双脚略有高低差；"
        "有头颈时保持自然比例并放松，可轻歪头、抬眼。"
        "已有衣物及柔软附属部分顺重力向下收拢垂坠，不铺地或横向展开。"
        "提拉处与躯干主体形成明确受力关系，不以承重站姿表达悬垂，不新增肢体、提拉道具或外来手掌"
    ),
}


class VideoScriptError(RuntimeError):
    """脚本不符合动作契约。"""


def _validate_duration(value: float) -> float:
    if not 0 < value <= ABSOLUTE_MAX_DURATION_SECONDS:
        raise ValueError(f"时长须在 (0, {ABSOLUTE_MAX_DURATION_SECONDS:g}] 秒内")
    return value


class _ActionSpec(BaseModel):
    """动作的共同需求；媒体制作规格由对应类型定义。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=32)
    system_slot: str = ""
    name: str = Field(default="", max_length=64)
    semantics: str = Field(default="", max_length=600)
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    feedback: str = ""

    def semantics_or(self) -> str:
        return self.semantics or SYSTEM_ACTION_SEMANTICS.get(self.system_slot or self.action, self.name)


class ImageActionSpec(_ActionSpec):
    media_type: Literal["image"] = "image"


class VideoActionSpec(_ActionSpec):
    media_type: Literal["video"] = "video"
    duration_seconds: float
    clip_kind: str = Field(pattern="^(loop|once)$")

    _spec_duration = field_validator("duration_seconds")(_validate_duration)


type ActionSpec = ImageActionSpec | VideoActionSpec


class ImageActionScriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image"] = "image"
    action: str
    image_prompt: str = Field(min_length=10, max_length=400)


class VideoActionScriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["video"] = "video"
    action: str
    pose_prompt: str = Field(min_length=10, max_length=400)
    motion_prompt: str = Field(min_length=10, max_length=600)
    # 时长与 clip_kind 由请求规格回填；模型只输出动作键与两段描述。
    duration_seconds: float = 0
    clip_kind: str = ""

    def with_spec(self, spec: VideoActionSpec) -> "VideoActionScriptEntry":
        return self.model_copy(
            update={
                "duration_seconds": _validate_duration(spec.duration_seconds),
                "clip_kind": spec.clip_kind,
            },
        )


type ActionScriptEntry = Annotated[ImageActionScriptEntry | VideoActionScriptEntry, Field(discriminator="media_type")]

_SCRIPT_ENTRY_ADAPTER = TypeAdapter(ActionScriptEntry)


def parse_action_script_entry(raw: str) -> ActionScriptEntry:
    return _SCRIPT_ENTRY_ADAPTER.validate_json(raw)


class ActionScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[VideoActionScriptEntry]


class ImageActionDescriptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ImageActionScriptEntry]


async def compose_action_image_descriptions(
    user_id: int,
    *,
    reference_image: str,
    identity: CharacterCardSnapshot,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    specs: list[ImageActionSpec],
    feedback: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> ImageActionDescriptions:
    payload = {
        "persona": persona_definition,
        "personality_tags": personality_tags,
        "outfit_description": outfit_description,
        "feedback": feedback,
        "actions": [
            {
                "action": spec.action,
                "name": spec.name,
                "description": spec.semantics_or(),
                "use_when": spec.use_when,
                "avoid_when": spec.avoid_when,
                "feedback": spec.feedback,
            }
            for spec in specs
        ],
    }
    wanted = [spec.action for spec in specs]
    last_error = "缺少有效动作图片描述"
    for _attempt in range(2):
        raw = await vision_chat(
            user_id,
            ACTION_IMAGE_DESCRIPTION_INSTRUCTIONS + "\n" + render_character_profile(identity),
            json.dumps(payload, ensure_ascii=False),
            reference_images=(reference_image,),
            before_submit=before_submit,
        )
        try:
            result = ImageActionDescriptions.model_validate(parse_llm_json(raw))
            keys = [entry.action for entry in result.actions]
            if len(keys) != len(wanted) or set(keys) != set(wanted):
                raise VideoScriptError("动作集合与请求不符")
            order = {key: index for index, key in enumerate(wanted)}
            result.actions.sort(key=lambda entry: order[entry.action])
            return result
        except (ValidationError, ValueError, VideoScriptError) as exc:
            last_error = str(exc)
            payload["validation_error"] = last_error
    raise VideoScriptError("动作图片描述生成失败，请重试") from ValueError(last_error)


def build_action_image_prompt(entry: ImageActionScriptEntry, identity: CharacterCardSnapshot) -> str:
    return (
        ACTION_IMAGE_TEMPLATE.format(pose=entry.image_prompt, margin_percent=ACTION_FRAME_MARGIN * 100)
        + "\n"
        + render_character_identity(identity)
    )


async def compose_action_script(
    user_id: int,
    *,
    reference_image: str,
    identity: CharacterCardSnapshot,
    persona_definition: dict[str, str],
    personality_tags: list[str],
    outfit_description: str,
    specs: list[VideoActionSpec],
    feedback: str = "",
    before_submit: Callable[[], Awaitable[None]] | None = None,
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
            VIDEO_ACTION_SCRIPT_INSTRUCTIONS + "\n" + render_character_profile(identity),
            json.dumps(payload, ensure_ascii=False),
            reference_images=(reference_image,),
            before_submit=before_submit,
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


async def inspect_peek_geometry(
    user_id: int,
    action: str,
    identity_uri: str,
    image_uri: str,
    *,
    before_submit: Callable[[], Awaitable[None]] | None = None,
) -> PeekGeometry | None:
    """定位成品图片的遮挡线；不可用时返回 None。"""
    if action not in ("peek_left", "peek_right") or not identity_uri or not image_uri:
        return None
    expected_side = "left" if action == "peek_left" else "right"
    try:
        raw = await vision_chat(
            user_id,
            PEEK_GEOMETRY_INSTRUCTIONS,
            json.dumps({"expected_side": expected_side}),
            reference_images=(identity_uri, image_uri),
            before_submit=before_submit,
        )
        payload = parse_llm_json(raw)
        if not isinstance(payload, dict) or payload.get("usable") is not True:
            return None
        geometry = PeekGeometry.model_validate(
            {"side": payload.get("side"), "cut_x": payload.get("cut_x"), "focus_rect": payload.get("focus_rect")},
        )
        return geometry if geometry.side == expected_side else None
    except LlmCallBlockedError:
        raise
    except Exception:
        logger.warning("peek geometry calibration failed", extra={"user_id": user_id, "action": action}, exc_info=True)
        return None


def build_video_prompt(entry: VideoActionScriptEntry, identity: CharacterCardSnapshot) -> str:
    is_loop = entry.clip_kind == "loop"
    prompt = VIDEO_PROMPT_SKELETON.format(
        motion=entry.motion_prompt,
        seconds=entry.duration_seconds,
        tail_clause=VIDEO_PROMPT_LOOP_TAIL if is_loop else "",
        cycle_clause=VIDEO_PROMPT_LOOP_CYCLE if is_loop else VIDEO_PROMPT_ONCE_CYCLE,
    )
    return prompt + "\n" + render_character_video_identity(identity)


def build_pose_prompt(entry: VideoActionScriptEntry, identity: CharacterCardSnapshot) -> str:
    return (
        VIDEO_ACTION_POSE_TEMPLATE.format(
            action=entry.motion_prompt,
            pose=entry.pose_prompt,
            margin_percent=ACTION_FRAME_MARGIN * 100,
        )
        + "\n"
        + render_character_identity(identity)
    )
