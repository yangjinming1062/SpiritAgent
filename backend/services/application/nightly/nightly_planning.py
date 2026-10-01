"""夜间自主规划、持久化动作账本与顺序执行。"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Annotated, Any, Literal, TypeGuard, get_args
from zoneinfo import ZoneInfo

from components import (
    NIGHTLY_PLANNING_REASONING_EFFORT,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    resolve_language,
    safe_json_loads,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    ActionDesignRequest,
    CharacterCardSnapshot,
    CompanionMoment,
    CompanionOutfit,
    CompanionScene,
    MomentKind,
    MomentSource,
    Persona,
    SceneOrigin,
    SceneStatus,
)
from modules.media import VideoGenJob
from modules.scheduler import NightlyActivityAction, NightlyActivityLog
from modules.settings import load_user_settings
from prompts.generation import NIGHTLY_SELF_VIDEO_REFERENCE_TEMPLATE
from prompts.nightly import NIGHTLY_FACT_TEXTS, OUTREACH_CONTEXT_TEMPLATES, PLANNING_SYSTEM_PROMPT
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.actions import (
    accept_proposal,
    build_action_context,
    schedule_accepted_proposal,
)
from services.application.generation import (
    activate_outfit,
    activate_scene,
    apply_outfit_override,
    build_self_image_prompt,
    confirm_outfit,
    create_outfit_draft,
    enqueue_video_job,
    generate_character_images,
    generate_images,
    load_self_visual_context,
    optional_outfit_image_reference,
    prepare_self_video_reference,
    resolve_image_gen_chain,
    resume_scene_generation,
    scene_generation_wait_seconds,
    schedule_scene_generation,
    video_generation_wait_seconds,
)
from services.contracts import MemoryScope
from services.domains.automation import create_job, remove_job
from services.domains.companion import (
    character_snapshot_is_current,
    get_scene_state,
    load_character_snapshot,
    load_persona_definition,
    render_character_appearance,
    render_character_identity,
    scene_environment,
)
from services.domains.journal import create_user_moment
from services.infrastructure.assets import save_companion_asset_async, unlink_companion_asset
from services.infrastructure.llm import UserLlmConfig, call_llm_once, resolve_provider_chain, synthesize_speech

logger = get_logger(__name__)

_SCENE_WAIT_SECONDS = 15 * 60
_POLL_SECONDS = 3.0
_ImageSize = Literal["1024x1024", "1024x1792", "1792x1024", "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9"]
_VideoAspectRatio = Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
_IMAGE_SIZES: tuple[str, ...] = get_args(_ImageSize)
_VIDEO_ASPECT_RATIOS: tuple[str, ...] = get_args(_VideoAspectRatio)
ActionStatus = Literal["succeeded", "partial", "skipped", "blocked", "failed", "interrupted"]
_TERMINAL_ACTION_STATUSES = frozenset(
    ("succeeded", "partial", "skipped", "blocked", "failed", "interrupted"),
)
_SUCCESS_ACTION_STATUSES = frozenset(("succeeded", "partial"))
# 在途动作记下这些子任务 id 后，进程重启可以核对原任务续跑而不重复付费。
_PROGRESS_KEYS = {"outfit.create": "outfit_id", "scene.create": "scene_id", "media.video": "job_id"}
_AUTONOMOUS_MEDIA = "companion.autonomous_media"
_AUTONOMOUS_VOICE = "companion.autonomous_voice"
_ACTION_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_ACTIONS = 8
_MAX_MEDIA_ACTIONS = 2
_MAX_PAID_ACTIONS = 4


class NightlyCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    phase: int
    description: str
    arguments: dict[str, Any]
    exclusive_group: str | None = None
    paid: bool = False


class DateContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_date: str
    tomorrow_date: str
    tomorrow_weekday: str
    next_7_days: list[str] = Field(default_factory=list)
    user_timezone: str


class _ActionArgs(BaseModel):
    """执行前校验规划参数：去除首尾空白后再核对长度与取值；不合规时整项失败，不截断或替换为默认值。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class OutfitWearArgs(_ActionArgs):
    outfit_id: int | str


class OutfitCreateArgs(_ActionArgs):
    description: str = Field(min_length=1, max_length=500)


class SceneActivateArgs(_ActionArgs):
    scene_id: int


class SceneCreateArgs(_ActionArgs):
    notes: str = Field(min_length=1)
    outfit_description: str | None = None


class MomentCreateArgs(_ActionArgs):
    title: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=500)
    emotion: str | None = Field(default=None, max_length=32)


class MediaImageArgs(_ActionArgs):
    prompt: str = Field(min_length=1, max_length=4000)
    title: str = Field(min_length=1, max_length=64)
    body: str = Field(default="", max_length=500)
    size: _ImageSize = "1024x1024"
    depicts_self: bool = Field(default=False, strict=True)
    narration: str | None = Field(default=None, max_length=800)


class MediaVideoArgs(_ActionArgs):
    prompt: str = Field(min_length=1, max_length=4000)
    title: str = Field(min_length=1, max_length=64)
    body: str = Field(default="", max_length=500)
    duration: Literal[6, 10] = 6
    aspect_ratio: _VideoAspectRatio = "16:9"
    depicts_self: bool = Field(default=False, strict=True)
    narration: str | None = Field(default=None, max_length=800)


class MediaVoiceArgs(_ActionArgs):
    text: str = Field(min_length=1, max_length=800)
    title: str = Field(min_length=1, max_length=64)
    body: str = Field(default="", max_length=500)


class OutreachScheduleArgs(_ActionArgs):
    name: str = Field(default="主动问候", max_length=100)
    # 规划给出用户本地时刻，时区换算由代码完成；schedule 仅供升级前已保存的计划恢复执行。
    local_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule: str | None = Field(default=None, min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_time(self) -> "OutreachScheduleArgs":
        if self.local_time is None and self.schedule is None:
            raise ValueError("outreach requires local_time")
        return self


class ActionDesignArgs(ActionDesignRequest):
    """夜间动作提案沿用动作设计契约，并按能力说明限制每条使用条件的长度。"""

    use_when: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=8)
    avoid_when: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=8)


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    capability: str
    phase: int
    depends_on: list[str] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # 规划产生的非法场景→换装依赖，执行时产生可诊断失败。
    illegal_outfit_deps: list[str] = Field(default_factory=list)


class NormalizedPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    rationale: str = ""
    actions: list[PlannedAction] = Field(default_factory=list)


class ActionExecutionResult(BaseModel):
    status: ActionStatus
    capability: str = ""
    fact: str | None = None
    reason: str | None = None
    warning: str | None = None
    dependencies: list[str] | None = None
    outfit_id: int | None = None
    scene_id: int | None = None
    moment_id: str | None = None
    job_id: int | None = None
    cron_job_id: int | None = None
    expires_at: str | None = None


class PlanningResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    rationale: str = ""
    actions: dict[str, ActionExecutionResult] = Field(default_factory=dict)


class PlanningPolicies(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outfit: str = "llm_may_replace"
    scene: str = "llm_may_replace"
    media: bool = True
    voice: bool = True


class PlanningProviders(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: bool = False
    image_reference: bool = False
    video: bool = False
    tts: bool = False


class PersonaContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    complete: bool = False
    definition: dict[str, Any] = Field(default_factory=dict)
    current_mood: str | None = None


class SceneContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    environment: dict[str, Any] = Field(default_factory=dict)
    library: list[dict[str, Any]] = Field(default_factory=list)
    generation_pending: bool = False


class WardrobeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    description: str = ""
    status: str
    active: bool = False


class RecentActionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    capability: str
    status: str
    fact: str | None = None


class RecentMomentSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    kind: str
    title: str
    source: str


class BlockedCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str


class PlanningContext(BaseModel):
    """规划输入（整体序列化为 autonomous_context）兼执行期共享的只读快照；plan_theme 在计划确定后写入。"""

    model_config = ConfigDict(extra="ignore")

    policies: PlanningPolicies
    providers: PlanningProviders
    selected_voice_id: str = ""
    language: str = ""
    persona: PersonaContext
    scene: SceneContext
    wardrobe: list[WardrobeItem] = Field(default_factory=list)
    # 动作库摘要：当前包就绪动作、在途提案与近期拒绝，供 action.design 评估缺口。
    actions: dict[str, Any] = Field(default_factory=dict)
    recent_autonomous_actions: list[RecentActionSummary] = Field(default_factory=list)
    recent_moments: list[RecentMomentSummary] = Field(default_factory=list)
    available_capabilities: list[NightlyCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    plan_theme: str = ""


@dataclass(frozen=True)
class _ActionRun:
    """单个账本动作的执行上下文；resume 为上次落账的进度，facts 为已成功动作的事实（执行中追加）。"""

    user_id: int
    row_id: int
    resume: dict[str, Any]
    context: PlanningContext
    date_context: DateContext
    facts: list[str]


_CAPABILITIES: tuple[NightlyCapability, ...] = (
    NightlyCapability(
        name="outfit.wear",
        phase=10,
        description="换上 wardrobe 中一套已就绪（status=ready）的外观。outfit_id 取 wardrobe 中实际存在的 id；已经穿着的无需重复选择。",
        arguments={
            "outfit_id": "integer：wardrobe 中 status=ready 且尚未穿着的外观 id，不填名称或自造 id。",
            "reason": "string（可选）：选择这套已有外观的具体理由。",
        },
        exclusive_group="outfit",
    ),
    NightlyCapability(
        name="outfit.create",
        phase=10,
        description="构思并生成一套新外观并换上；只在已有外观都不合适或有特殊节点时使用。",
        arguments={
            "description": "string（非空，最多 500 字符）：完整的新造型设计，包括服装、配色及需要改变的发型、妆容或配饰；保持角色固定身份，不写场景或动作。",
            "reason": "string（可选）：已有外观不能满足的需要，以及本次新建外观的依据。",
        },
        exclusive_group="outfit",
        paid=True,
    ),
    NightlyCapability(
        name="scene.activate",
        phase=20,
        description="启用 scene.library 中适合的已有场景，不消耗生图额度；已是当前场景的无需重复启用。"
        "场景画面中的穿着保持该场景创建时的样子，换装动作不会改变它，因此不依赖换装动作。",
        arguments={
            "scene_id": "integer：scene.library 中实际存在且可启用的场景 id，不填名称或自造 id。",
            "reason": "string（可选）：该场景适合当前安排的具体理由。",
        },
        exclusive_group="scene",
    ),
    NightlyCapability(
        name="scene.create",
        phase=20,
        description="已有场景不适合时创建并启用新场景。notes 描述地点、环境与活动，必须非空；"
        "场景穿着只由 outfit_description 决定：填写本次明确的完整着装设计，无着装要求时省略并沿用创建时的当前外观；"
        "不通过依赖换装动作表达场景穿着。",
        arguments={
            "notes": "string（非空）：地点、环境和角色活动，描述一个可见瞬间及必要的接触、支撑关系；不重新设计角色外貌，着装写入 outfit_description。",
            "outfit_description": "string（可选）：本次完整造型，涵盖服装、配色及所需发型、妆容、鞋履和配饰；局部修改先合并为完整描述，无着装要求时省略，沿用创建时的当前外观。",
            "reason": "string（可选）：已有场景不合适、需要新建场景的依据。",
        },
        exclusive_group="scene",
        paid=True,
    ),
    NightlyCapability(
        name="moment.create",
        phase=30,
        description="写下一条文字片刻，例如一张便笺、愿望或值得纪念的小事。",
        arguments={
            "title": "string（非空，最多 64 字符）：片刻列表中展示的简短标题。",
            "body": "string（非空，最多 500 字符）：供用户阅读的片刻正文，以角色口吻表达便笺、愿望或有依据的感受，不把计划写成已经发生的共同经历。",
            "emotion": "string（可选，最多 32 字符）：这段文字表达的情绪，未明确时省略。",
        },
        exclusive_group="text_moment",
    ),
    NightlyCapability(
        name="media.image",
        phase=30,
        description="创作保存到片刻的图片；depicts_self=true 时使用角色身份与执行时的当前外观。narration 是可选的独立语音，不会让图片中的人物活动。",
        arguments={
            "prompt": "string（非空，最多 4000 字符）：独立完整的画面描述，包含主体、一个可见瞬间、构图、环境与光照；动作写清位置、接触和支撑。仅将需要画出的文字用引号标注并说明位置，不把限制语句写成画面文字。出镜时不复述固定外貌或改变当前造型。",
            "title": "string（非空，最多 64 字符）：图片片刻的展示标题，不是画面内文字。",
            "body": "string（可选，最多 500 字符）：图片片刻的文字配文，不作为生图指令或语音正文。",
            "size": f"string（可选，默认 1024x1024）：输出尺寸或比例，从 {' / '.join(sorted(_IMAGE_SIZES))} 中选一项。",
            "depicts_self": "boolean（可选，默认 false）：角色本人出现在画面时填 true，自动提供身份与当前造型；画面不包含本人时填 false。",
            "narration": "string（可选，最多 800 字符）：随图片附带的独立语音正文，填写实际要说的话，不写制作指令；不需要语音时省略。",
            "reason": "string（可选）：准备这张图片的情境依据与表达意图。",
        },
        paid=True,
    ),
    NightlyCapability(
        name="media.video",
        phase=30,
        description="创作保存到片刻的短视频；depicts_self=true 时先按视频要求生成符合角色固定外形与执行时当前外观的起始画面，"
        "再保持其身份与穿着生成视频，需要 image_reference=true。"
        "仅当确实要展示今晚新换的外观时才依赖对应换装动作，场景穿着不构成这种依赖。"
        "narration 是使用当前音色生成的独立音轨，不保证口型同步，长度要能在所选时长内自然说完。",
        arguments={
            "prompt": "string（非空，最多 4000 字符）：独立完整的视频要求，写清主体、环境、起始姿态与物体位置、随后动作及镜头变化，动作量适合所选时长；出镜时保持固定身份与当前造型，独立旁白写入 narration。",
            "title": "string（非空，最多 64 字符）：视频片刻的展示标题，不是视频字幕。",
            "body": "string（可选，最多 500 字符）：视频片刻的文字配文，不作为视频指令或语音正文。",
            "duration": "integer（可选，默认 6）：视频时长，单位为秒，只能选 6 或 10。",
            "aspect_ratio": f"string（可选，默认 16:9）：视频画幅比例，从 {' / '.join(sorted(_VIDEO_ASPECT_RATIOS))} 中选一项。",
            "depicts_self": "boolean（可选，默认 false）：角色本人出现在视频时填 true，自动按身份与当前造型制作首帧；不包含本人时填 false。",
            "narration": "string（可选，最多 800 字符）：附加的独立旁白正文，填写实际要说的话，不是人物口型指令；不需要旁白时省略。",
            "reason": "string（可选）：准备这段视频的情境依据与表达意图。",
        },
        paid=True,
    ),
    NightlyCapability(
        name="media.voice",
        phase=30,
        description="用角色当前音色录制一段语音心意并永久保存到片刻。",
        arguments={
            "text": "string（非空，最多 800 字符）：使用角色当前音色朗读的语音正文，填写实际要说的话，不写制作指令。",
            "title": "string（非空，最多 64 字符）：语音片刻的展示标题，不参与朗读。",
            "body": "string（可选，最多 500 字符）：语音片刻的文字配文，不参与朗读。",
            "reason": "string（可选）：留下这段语音的情境依据与表达意图。",
        },
        paid=True,
    ),
    NightlyCapability(
        name="outreach.schedule",
        phase=40,
        description="安排次日主动联系；从计划时间起等待用户在线，最晚保留到用户本地次日结束。",
        arguments={
            "name": "string（可选，最多 100 字符）：主动联系任务的简短名称，省略时为主动问候。",
            "local_time": "string（非空，HH:MM，24 小时制）：用户本地时间，在 tomorrow_date 当天这一时刻开始等待用户在线；时区换算由系统完成。",
            "prompt": "string（非空，最多 4000 字符）：触发时交给角色的独立任务说明，写清联系缘由、交流目标和必要背景；不依赖本轮规划上下文，不把尚未完成的准备描述为既成事实。",
        },
        exclusive_group="outreach",
    ),
    NightlyCapability(
        name="action.design",
        phase=25,
        description="为当前形象提交一个可反复使用的新动作提案（如张开双臂、打哈欠、一段舞蹈），不是一次性视频作品。"
        "先查看 autonomous_context.actions.library 的动作内容和适用条件，以及同处的 in_flight_proposals、recent_rejections；"
        "提案的 design 是原设计，review_reason 是评审理由，status 与 action_status 分别说明评审和制作进展；"
        "action_outfit 是动作素材中的着装，新动作按它设计。"
        "确有缺口才使用；无明确价值时选择不使用。"
        "name 是动作显示名称；motion_description 写单主体可见的姿态、节奏与神态，"
        "不含场景、镜头或产品概念；use_when / avoid_when 说明何时适用或避免；reason 说明为何需要新动作。"
        "duration_seconds 为 1–15 的整秒数；clip_kind 为 loop（连续运动周期）或 once（完整动作自然收束）。"
        "once 制作后仍可重复使用。单主体原地运动、固定镜头、全身入画，保持身体结构与穿着，"
        "不新增人物、道具、场景、对话或音轨。动作素材属于当前使用的动作形象，今晚的换装不会改变它，因此不依赖换装动作。"
        "受理仅表示申请成功，评审和制作随后进行；"
        "后续片刻或联系不能以依赖此项为依据宣称动作已做好或已表演。",
        arguments={
            "name": "string（1–64 字符）：动作的简短显示名称，不是动作标识或运动脚本。",
            "motion_description": "string（10–600 字符）：单主体可见的姿态、运动过程、节奏与神态；不写场景、镜头或制作流程，保持已有身体结构与穿着。",
            "use_when": "list[string]（可选，最多 8 项，每项最多 120 字符）：适合使用该动作的交流情境，省略或空列表表示没有补充适用条件。",
            "avoid_when": "list[string]（可选，最多 8 项，每项最多 120 字符）：应避免使用该动作的情境，省略或空列表表示没有补充避免条件。",
            "reason": "string（1–400 字符）：现有动作无法满足的表达需要，以及本次新增动作的具体价值。",
            "duration_seconds": "integer（1–15）：单个动作片段的时长，单位为秒；按该时长安排循环周期或完整的一次性动作。",
            "clip_kind": "string：loop 表示首尾连续的循环，once 表示有自然收束的一次完整动作；选其中一项，once 制作后也可重复使用。",
        },
        paid=True,
    ),
)
_CAPABILITY_BY_NAME = {cap.name: cap for cap in _CAPABILITIES}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _invalid_arguments(exc: ValidationError) -> ActionExecutionResult:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors(include_url=False)
    )
    return ActionExecutionResult(status="failed", reason=f"invalid arguments: {details}")


def _is_action_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= 48 and not _ACTION_ID_PATTERN.search(value)


def _switch_on(settings: dict[str, Any], key: str) -> bool:
    return settings.get(key, True) is True


def _policies(persona: Persona | None, settings: dict[str, Any]) -> PlanningPolicies:
    return PlanningPolicies(
        outfit=persona.outfit_policy if persona is not None else "llm_may_replace",
        scene=persona.scene_policy if persona is not None else "llm_may_replace",
        media=_switch_on(settings, _AUTONOMOUS_MEDIA),
        voice=_switch_on(settings, _AUTONOMOUS_VOICE),
    )


def _policy_block(capability: str, policies: PlanningPolicies) -> str | None:
    """用户政策与开关对能力的拦截原因；规划时的可用性与执行前的复核共用这一规则。"""
    if capability.startswith("outfit.") and policies.outfit == "locked":
        return "outfit policy locked"
    if capability.startswith("scene.") and policies.scene == "locked":
        return "scene policy locked"
    if capability in ("media.image", "media.video") and not policies.media:
        return "autonomous media disabled"
    if capability == "media.voice" and not policies.voice:
        return "autonomous voice disabled"
    return None


async def _provider_available(
    db: AsyncSession,
    user_id: int,
    service_type: str,
) -> bool:
    try:
        return bool(await resolve_provider_chain(db, user_id, service_type))
    except Exception:
        logger.warning(
            "nightly capability availability check failed",
            extra={"user_id": user_id, "service_type": service_type},
            exc_info=True,
        )
        return False


async def _reference_image_provider_available(
    db: AsyncSession,
    user_id: int,
) -> bool:
    try:
        chain, _ = await resolve_image_gen_chain(db, user_id, has_reference=True)
        return bool(chain)
    except Exception:
        logger.warning(
            "nightly reference-image capability check failed",
            extra={"user_id": user_id},
            exc_info=True,
        )
        return False


def _capability_availability(
    context: PlanningContext,
) -> tuple[list[NightlyCapability], list[BlockedCapability]]:
    providers = context.providers
    persona_ready = bool(context.persona.complete)
    has_ready_outfit = any(item.status == "ready" for item in context.wardrobe)
    # 政策拦截由 _policy_block 统一判断；这里只列库存与供应商条件，原因文案覆盖两类拦截。
    rules: dict[str, tuple[bool, str]] = {
        "outfit.wear": (has_ready_outfit, "换装已锁定或没有 ready 外观"),
        "outfit.create": (providers.image_reference and persona_ready, "换装已锁定或形象/生图不可用"),
        "scene.activate": (bool(context.scene.library), "场景已锁定或没有可用场景"),
        "scene.create": (
            providers.image_reference and persona_ready and not context.scene.generation_pending,
            "场景已锁定、形象/生图不可用或已有场景正在生成",
        ),
        "moment.create": (True, ""),
        "media.image": (providers.image, "自主心意或生图供应商不可用"),
        "media.video": (providers.video, "自主心意或视频供应商不可用"),
        "media.voice": (providers.tts, "夜间自主语音或 TTS 供应商不可用"),
        "outreach.schedule": (True, ""),
        "action.design": (
            bool(context.actions.get("pack_id")) and providers.video,
            "当前形象还没有可用的动作素材，或视频供应商不可用",
        ),
    }
    available: list[NightlyCapability] = []
    blocked: list[BlockedCapability] = []
    for capability in _CAPABILITIES:
        ready, reason = rules[capability.name]
        if ready and _policy_block(capability.name, context.policies) is None:
            available.append(capability)
        else:
            blocked.append(BlockedCapability(name=capability.name, reason=reason))
    return available, blocked


async def _collect_context(user_id: int, timezone: ZoneInfo) -> PlanningContext:
    async with SESSION_LOCAL() as db:
        persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
        outfits = (
            await db.scalars(
                select(CompanionOutfit)
                .where(CompanionOutfit.user_id == user_id)
                .order_by(CompanionOutfit.created_at.desc()),
            )
        ).all()
        scene = await get_scene_state(db, user_id)
        scene_rows = (
            await db.scalars(
                select(CompanionScene)
                .where(
                    CompanionScene.user_id == user_id,
                    CompanionScene.status == SceneStatus.READY.value,
                )
                .order_by(CompanionScene.id.desc()),
            )
        ).all()
        character = await load_character_snapshot(db, user_id)
        settings = await load_user_settings(
            db,
            user_id,
            (_AUTONOMOUS_MEDIA, _AUTONOMOUS_VOICE, "companion.voice_id", "language"),
        )
        image_available = await _provider_available(db, user_id, "image_gen")
        reference_image_available = await _reference_image_provider_available(db, user_id)
        video_available = await _provider_available(db, user_id, "video_gen")
        tts_available = await _provider_available(db, user_id, "tts")
        recent_actions = (
            await db.scalars(
                select(NightlyActivityAction)
                .where(NightlyActivityAction.user_id == user_id)
                .order_by(NightlyActivityAction.id.desc())
                .limit(40),
            )
        ).all()
        recent_moments = (
            await db.execute(
                select(CompanionMoment.occurred_at, CompanionMoment.kind, CompanionMoment.title, CompanionMoment.source)
                .where(CompanionMoment.user_id == user_id)
                .order_by(CompanionMoment.occurred_at.desc())
                .limit(20),
            )
        ).all()
        # 动作库摘要在会话生命周期内读取，避免 session 关闭后重开未托管事务。
        action_snapshot = await build_action_context(db, user_id)

    language = resolve_language(settings.get("language"))
    definition = load_persona_definition(persona)
    if character is not None:
        definition["fixed_features"] = render_character_appearance(character, language=language)
    context = PlanningContext(
        policies=_policies(persona, settings),
        providers=PlanningProviders(
            image=image_available,
            image_reference=reference_image_available,
            video=video_available,
            tts=tts_available,
        ),
        selected_voice_id=str(settings.get("companion.voice_id") or ""),
        language=language,
        persona=PersonaContext(
            complete=bool(persona and persona.is_complete),
            definition=definition,
            current_mood=persona.current_mood if persona is not None else None,
        ),
        scene=SceneContext(
            environment=scene_environment(scene),
            library=[{"id": row.id, "title": row.title, "description": row.description} for row in scene_rows],
            generation_pending=scene.pending is not None,
        ),
        wardrobe=[
            WardrobeItem(
                id=outfit.id,
                name=outfit.name,
                description=outfit.description or "",
                status=outfit.status,
                active=outfit.active,
            )
            for outfit in outfits
        ],
        actions={
            "pack_id": action_snapshot.pack_id,
            "catalog_version": action_snapshot.catalog_version,
            "action_outfit": action_snapshot.outfit_description,
            "library": action_snapshot.ready_actions,
            "in_flight_proposals": action_snapshot.in_flight_proposals,
            "recent_rejections": action_snapshot.recent_rejections,
        },
        recent_autonomous_actions=[
            RecentActionSummary(
                date=row.target_date.isoformat(),
                capability=row.capability,
                status=row.status,
                fact=(row.result or {}).get("fact"),
            )
            for row in recent_actions
        ],
        # 与动作的 target_date 一样按用户本地日标注。
        recent_moments=[
            RecentMomentSummary(
                date=occurred_at.astimezone(timezone).date().isoformat(),
                kind=kind,
                title=title,
                source=source,
            )
            for occurred_at, kind, title, source in recent_moments
        ],
    )
    context.available_capabilities, context.blocked_capabilities = _capability_availability(context)
    return context


def _normalize_plan(parsed: Any, context: PlanningContext) -> NormalizedPlan:
    if not isinstance(parsed, dict):
        raise TypeError("nightly planning returned invalid JSON")
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        raise ValueError("nightly plan requires an actions array; use [] for no action")
    seen_ids: set[str] = set()
    for raw in raw_actions:
        if not isinstance(raw, dict):
            raise ValueError("Each planned action must be an object")
        action_id = raw.get("id")
        if not _is_action_id(action_id) or action_id in seen_ids:
            raise ValueError("Each action requires a unique ID of 1-48 letters, digits, underscores or hyphens")
        seen_ids.add(action_id)
    available_names = {item.name for item in context.available_capabilities}
    seen_groups: set[str] = set()
    media_count = 0
    paid_count = 0
    actions: list[PlannedAction] = []
    for raw in raw_actions:
        if len(actions) >= _MAX_ACTIONS:
            break
        capability_name = _text(raw.get("capability"))
        spec = _CAPABILITY_BY_NAME.get(capability_name)
        if spec is None or capability_name not in available_names:
            continue
        if spec.exclusive_group and spec.exclusive_group in seen_groups:
            continue
        if spec.paid and paid_count >= _MAX_PAID_ACTIONS:
            continue
        is_media = capability_name.startswith("media.")
        if is_media and media_count >= _MAX_MEDIA_ACTIONS:
            continue
        # 能力或预算过滤不删除前置条件；执行端对未完成的依赖跳过后续动作。
        # null 视为没有依赖；依赖写法无效只跳过该动作，依赖它的后续动作在执行端因前置未完成而跳过。
        dependencies = raw.get("depends_on") or []
        if not isinstance(dependencies, list) or not all(_is_action_id(dep) for dep in dependencies):
            continue
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        if (
            capability_name in ("media.image", "media.video")
            and args.get("depicts_self") is True
            and not context.providers.image_reference
        ):
            continue
        if is_media:
            media_count += 1
        if spec.paid:
            paid_count += 1
        if spec.exclusive_group:
            seen_groups.add(spec.exclusive_group)
        actions.append(
            PlannedAction(
                id=raw["id"],
                capability=capability_name,
                phase=spec.phase,
                depends_on=dependencies,
                arguments=args,
            ),
        )
    outfit_ids = {action.id for action in actions if action.capability.startswith("outfit.")}
    for action in actions:
        if action.capability.startswith("scene."):
            action.illegal_outfit_deps = [dep for dep in action.depends_on if dep in outfit_ids]
    # 稳定排序：同阶段保持规划顺序。
    actions.sort(key=lambda action: action.phase)
    return NormalizedPlan(
        theme=_text(parsed.get("theme")),
        rationale=_text(parsed.get("rationale")),
        actions=actions,
    )


async def _stored_plan(log_id: int) -> NormalizedPlan | None:
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
    plan = log.payload.get("nightly_plan") if log is not None and log.payload else None
    return NormalizedPlan.model_validate(plan) if plan is not None else None


async def _persist_plan(log_id: int, plan: NormalizedPlan) -> None:
    """计划与动作账本同一事务落库；恢复时直接执行账本，不重新规划。"""
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log is None:
            return
        log.payload = {**(log.payload or {}), "nightly_plan": plan.model_dump()}
        db.add_all(
            NightlyActivityAction(
                log_id=log_id,
                user_id=log.user_id,
                target_date=log.target_date,
                action_key=action.id,
                capability=action.capability,
                phase=action.phase,
                arguments={
                    "depends_on": action.depends_on,
                    "values": action.arguments,
                    **({"illegal_outfit_deps": action.illegal_outfit_deps} if action.illegal_outfit_deps else {}),
                },
            )
            for action in plan.actions
        )
        await db.commit()


async def _load_action_rows(log_id: int) -> list[NightlyActivityAction]:
    """读取动作账本，并收敛上次进程中断时的在途动作。"""
    async with SESSION_LOCAL() as db:
        rows = list(
            (
                await db.scalars(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.log_id == log_id)
                    .order_by(NightlyActivityAction.phase, NightlyActivityAction.id),
                )
            ).all(),
        )
        for row in rows:
            if row.status != "running":
                continue
            progress_key = _PROGRESS_KEYS.get(row.capability)
            if progress_key and isinstance(row.result, dict) and row.result.get(progress_key):
                # 继续同一业务任务；执行器依据子管线的持久化检查点恢复。
                row.status = "pending"
            else:
                row.status = "interrupted"
                row.finished_at = utc_now()
                row.result = {
                    "status": "interrupted",
                    "reason": "上次进程在动作执行中终止；没有可核对的内部任务 id，为避免重复付费或重复副作用，本动作不自动重放",
                }
        await db.commit()
    return rows


async def _set_action_state(
    action_id: int,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        row = await db.get(NightlyActivityAction, action_id)
        if row is None:
            return
        row.status = status
        if status == "running":
            row.started_at = utc_now()
        if status in _TERMINAL_ACTION_STATUSES:
            row.finished_at = utc_now()
        if result is not None:
            row.result = result
        log = await db.get(NightlyActivityLog, row.log_id)
        if log is not None:
            log.updated_at = utc_now()
        await db.commit()


async def _save_progress(run: _ActionRun, **progress: int | str) -> None:
    """付费子任务一经提交就把可核对的 id 写入账本，重启后续跑原任务而不重复提交。"""
    await _set_action_state(run.row_id, "running", {"status": "running", **progress})


async def _runtime_block_reason(user_id: int, capability: str, resume: dict[str, Any]) -> str | None:
    """动作真正执行前重读总控、政策与开关，避免长耗时计划期间用户关锁后仍产生副作用。"""
    async with SESSION_LOCAL() as db:
        user = await db.get(User, user_id)
        persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
        settings = await load_user_settings(db, user_id, (_AUTONOMOUS_MEDIA, _AUTONOMOUS_VOICE))
        pending_scene_id = (
            await db.scalar(
                select(CompanionScene.id)
                .where(
                    CompanionScene.user_id == user_id,
                    CompanionScene.status == SceneStatus.PENDING.value,
                )
                .limit(1),
            )
            if capability == "scene.create"
            else None
        )
    if user is None or not user.nightly_activity_enabled:
        return "nightly activity disabled"
    if reason := _policy_block(capability, _policies(persona, settings)):
        return reason
    if pending_scene_id is not None and pending_scene_id != resume.get("scene_id"):
        return "another scene generation is already pending"
    return None


def _fact(run: _ActionRun, key: str, **values: str) -> str:
    return NIGHTLY_FACT_TEXTS[resolve_language(run.context.language)][key].format(**values)


def _scene_fact(run: _ActionRun, scene: CompanionScene) -> str:
    return _fact(run, "scene", title=scene.title, description=scene.description)


async def _execute_outfit_wear(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        outfit_id = int(OutfitWearArgs.model_validate(args).outfit_id)
    except (ValidationError, TypeError, ValueError):
        return ActionExecutionResult(status="failed", reason="invalid outfit_id")
    listed = next((item for item in run.context.wardrobe if item.id == outfit_id and item.status == "ready"), None)
    if listed is None:
        return ActionExecutionResult(status="failed", reason="outfit was not a listed ready item")
    if listed.active:
        return ActionExecutionResult(status="skipped", reason="outfit is already active")
    async with SESSION_LOCAL() as db:
        outfit = await activate_outfit(db, run.user_id, outfit_id)
    return ActionExecutionResult(
        status="succeeded",
        outfit_id=outfit.id,
        fact=_fact(run, "outfit_wear", name=outfit.name),
    )


async def _execute_outfit_create(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        description = OutfitCreateArgs.model_validate(args).description
    except ValidationError as exc:
        return _invalid_arguments(exc)
    resume_outfit_id = run.resume.get("outfit_id")
    if resume_outfit_id is None:
        async with SESSION_LOCAL() as db:
            draft = await create_outfit_draft(db, run.user_id, description=description)
            outfit = await confirm_outfit(db, run.user_id, draft.id)
        await _save_progress(run, outfit_id=outfit.id)
    else:
        async with SESSION_LOCAL() as db:
            outfit = await db.get(CompanionOutfit, int(resume_outfit_id))
        # 确认是同步的：恢复时缺失或未就绪说明外观已被删除 / 重绘回草稿，不能盲目重做付费生成。
        if outfit is None or outfit.user_id != run.user_id or outfit.status != "ready":
            return ActionExecutionResult(
                status="failed",
                outfit_id=int(resume_outfit_id),
                reason="outfit is not ready after confirm",
            )
    if not outfit.active:
        async with SESSION_LOCAL() as db:
            outfit = await activate_outfit(db, run.user_id, outfit.id, require_current_identity=True)
    display_name = outfit.name if outfit.name != "新外观" else description[:40]
    return ActionExecutionResult(
        status="succeeded",
        outfit_id=outfit.id,
        fact=_fact(run, "outfit_create", name=display_name),
    )


async def _wait_for_scene(
    user_id: int,
    scene_id: int,
) -> CompanionScene | None:
    started = monotonic()
    deadline = started + _SCENE_WAIT_SECONDS
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = await db.get(CompanionScene, scene_id)
        if (
            row is None
            or row.user_id != user_id
            or row.status
            in (
                SceneStatus.FAILED.value,
                SceneStatus.CANCELLED.value,
                SceneStatus.DESCRIPTION_FAILED.value,
            )
        ):
            return None
        deadline = max(deadline, started + scene_generation_wait_seconds(row))
        if row.status == SceneStatus.READY.value:
            return row if row.activated_at is not None else None
        await asyncio.sleep(_POLL_SECONDS)
    return None


async def _execute_scene_activate(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        scene_id = SceneActivateArgs.model_validate(args).scene_id
    except ValidationError as exc:
        return _invalid_arguments(exc)
    current = run.context.scene.environment.get("current")
    # 启用已在使用的场景不会发生变化，不能记成一次场景切换。
    if isinstance(current, dict) and current.get("id") == scene_id:
        return ActionExecutionResult(status="skipped", scene_id=scene_id, reason="scene is already current")
    async with SESSION_LOCAL() as db:
        row = await activate_scene(db, run.user_id, scene_id, origin=SceneOrigin.NIGHTLY.value)
    return ActionExecutionResult(status="succeeded", scene_id=row.id, fact=_scene_fact(run, row))


async def _execute_scene_create(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = SceneCreateArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    scene_id = run.resume.get("scene_id")
    if scene_id is None:
        row = await schedule_scene_generation(
            run.user_id,
            origin=SceneOrigin.NIGHTLY.value,
            notes=parsed_args.notes,
            outfit_description=parsed_args.outfit_description or None,
            auto_activate=True,
        )
        scene_id = row.id
        await _save_progress(run, scene_id=scene_id)
    else:
        scene_id = int(scene_id)
        await resume_scene_generation(run.user_id, scene_id)
    ready = await _wait_for_scene(run.user_id, scene_id)
    if ready is None:
        return ActionExecutionResult(
            status="interrupted",
            scene_id=scene_id,
            reason="场景尚未确认启用，不能记录到达事实；可查询原任务",
        )
    return ActionExecutionResult(status="succeeded", scene_id=ready.id, fact=_scene_fact(run, ready))


async def _wait_for_video(user_id: int, job_id: int) -> VideoGenJob | None:
    deadline: float | None = None
    while deadline is None or monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = await db.get(VideoGenJob, job_id)
        if row is None or row.user_id != user_id or row.status in ("failed", "result_unknown"):
            return None
        if row.status == "succeeded":
            return row
        if deadline is None:
            deadline = monotonic() + video_generation_wait_seconds(row)
        await asyncio.sleep(_POLL_SECONDS)
    return None


def _audio_extension(mime: str) -> str:
    normalized = mime.lower()
    if "wav" in normalized:
        return "wav"
    if "ogg" in normalized or "opus" in normalized:
        return "ogg"
    if "mp4" in normalized or "m4a" in normalized:
        return "m4a"
    return "mp3"


async def _voice_asset(run: _ActionRun, text: str) -> tuple[str, str]:
    result = await synthesize_speech(
        run.user_id,
        text,
        run.context.selected_voice_id,
        run.context.language,
    )
    path = await save_companion_asset_async(
        result.audio,
        user_id=run.user_id,
        label="nightly_voice",
        ext=_audio_extension(result.mime),
    )
    return path, result.voice or ""


async def _narration_switches_on(user_id: int) -> bool:
    """媒体生成耗时较长，合成旁白前再次核对心意与语音开关。"""
    async with SESSION_LOCAL() as db:
        settings = await load_user_settings(db, user_id, (_AUTONOMOUS_MEDIA, _AUTONOMOUS_VOICE))
    return _switch_on(settings, _AUTONOMOUS_MEDIA) and _switch_on(settings, _AUTONOMOUS_VOICE)


async def _optional_narration(
    run: _ActionRun,
    narration: str | None,
) -> tuple[str | None, str | None, str | None]:
    """返回 (音频路径, 音色, 失败原因)；旁白缺失不阻断媒体发布，只把动作记为部分成功。"""
    if not narration:
        return None, None, None
    if isinstance(run.resume.get("audio_path"), str):
        return run.resume["audio_path"], str(run.resume.get("voice_id") or ""), None
    if not run.context.providers.tts or not await _narration_switches_on(run.user_id):
        return None, None, "narration unavailable or disabled"
    try:
        audio_path, voice_id = await _voice_asset(run, narration)
        return audio_path, voice_id, None
    except Exception as exc:
        logger.warning(
            "nightly media narration failed",
            extra={"user_id": run.user_id, "error": str(exc)},
            exc_info=True,
        )
        return None, None, str(exc)


async def _identity_current(user_id: int, identity: CharacterCardSnapshot) -> bool:
    async with SESSION_LOCAL() as db:
        return await character_snapshot_is_current(db, user_id, identity)


async def _discard_assets(*paths: str | None) -> None:
    """删除未发布的生成资产：它们只在片刻保存成功后才被引用，否则会成为无人引用的文件。"""
    for path in paths:
        if path:
            await asyncio.to_thread(unlink_companion_asset, path)


async def _execute_media_image(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = MediaImageArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    size = parsed_args.size
    identity: CharacterCardSnapshot | None = None
    if parsed_args.depicts_self:
        visual = await load_self_visual_context(run.user_id)
        identity = visual.identity
        plan = apply_outfit_override(visual, None)
        outfit = await optional_outfit_image_reference(plan, run.user_id)
        urls = await generate_character_images(
            build_self_image_prompt(plan, parsed_args.prompt, has_outfit_reference=bool(outfit)),
            size=size,
            user_id=run.user_id,
            reference_image=visual.reference_image,
            identity_reference=visual.reference_image,
            secondary_reference_image=outfit,
            identity_text=render_character_identity(identity),
        )
    else:
        urls = await generate_images(parsed_args.prompt, size=size, user_id=run.user_id, persist_user_assets=True)

    async def discard_if_stale(audio_path: str | None = None) -> bool:
        # 出镜图片在生成与旁白期间角色外形可能已更新，旧参考的结果不发布。
        if identity is None or await _identity_current(run.user_id, identity):
            return False
        await _discard_assets(*urls, audio_path)
        return True

    stale = ActionExecutionResult(status="blocked", reason="角色外形已更新，旧参考生成的图片未发布")
    if await discard_if_stale():
        return stale
    audio_path, voice_id, narration_error = await _optional_narration(run, parsed_args.narration)
    if await discard_if_stale(audio_path):
        return stale
    async with SESSION_LOCAL() as db:
        try:
            moment = await create_user_moment(
                db,
                run.user_id,
                title=parsed_args.title,
                body=parsed_args.body,
                media_url=urls[0],
                media_type="image",
                audio_url=audio_path,
                media_metadata={"voice_id": voice_id} if voice_id else None,
                kind=MomentKind.SCENE.value,
                source=MomentSource.NIGHTLY.value,
            )
        except Exception:
            await _discard_assets(*urls, audio_path)
            raise
    return ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        moment_id=moment.id,
        fact=_fact(run, "media_image", title=parsed_args.title),
        warning=narration_error or None,
    )


async def _execute_media_video(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = MediaVideoArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    duration = parsed_args.duration
    aspect_ratio = parsed_args.aspect_ratio
    resume_job_id = run.resume.get("job_id")
    if resume_job_id is None:
        prompt = parsed_args.prompt
        first_frame = None
        visual = None
        if parsed_args.depicts_self:
            visual = await load_self_visual_context(run.user_id)
            first_frame = await prepare_self_video_reference(
                apply_outfit_override(visual, None),
                run.user_id,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
            prompt = (
                NIGHTLY_SELF_VIDEO_REFERENCE_TEMPLATE.format(prompt=prompt)
                + "\n"
                + render_character_identity(visual.identity)
            )
        async with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=run.user_id,
                session_id=None,
                prompt=prompt,
                duration=duration,
                resolution="768P",
                first_frame_image=first_frame,
                aspect_ratio=aspect_ratio,
                identity_reference_path=visual.reference_path if visual is not None else None,
                identity=visual.identity if visual is not None else None,
            )
        job_id = job.id
        if job.status == "result_unknown":
            return ActionExecutionResult(status="failed", job_id=job_id, reason=job.error_message)
        await _save_progress(run, job_id=job_id)
    else:
        job_id = int(resume_job_id)
    completed = await _wait_for_video(run.user_id, job_id)
    if completed is None or not completed.video_url:
        return ActionExecutionResult(
            status="failed",
            job_id=job_id,
            reason="video generation failed or timed out",
        )
    audio_path, voice_id, narration_error = await _optional_narration(run, parsed_args.narration)
    if audio_path:
        await _save_progress(run, job_id=job_id, audio_path=audio_path, voice_id=voice_id or "")
    if parsed_args.depicts_self:
        # 视频任务交付时已核对身份；旁白合成期间外形可能再次更新，发布前按任务冻结的角色卡复核。
        params = safe_json_loads(completed.params_json or "{}", default={})
        snapshot = params.get("identity_snapshot") if isinstance(params, dict) else None
        if not snapshot or not await _identity_current(run.user_id, CharacterCardSnapshot.model_validate(snapshot)):
            # 视频文件归视频任务所有，只清理本动作合成的旁白。
            await _discard_assets(audio_path)
            return ActionExecutionResult(
                status="blocked",
                job_id=job_id,
                reason="角色外形已更新，旧参考生成的视频未发布",
            )
    async with SESSION_LOCAL() as db:
        try:
            moment = await create_user_moment(
                db,
                run.user_id,
                title=parsed_args.title,
                body=parsed_args.body,
                media_url=completed.video_url,
                media_type="video",
                audio_url=audio_path,
                media_metadata={"voice_id": voice_id, "narration": parsed_args.narration or ""} if voice_id else None,
                kind=MomentKind.SCENE.value,
                source=MomentSource.NIGHTLY.value,
            )
        except Exception:
            await _discard_assets(audio_path)
            raise
    return ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        job_id=job_id,
        moment_id=moment.id,
        fact=_fact(run, "media_video", title=parsed_args.title),
        warning=narration_error or None,
    )


async def _execute_media_voice(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = MediaVoiceArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    audio_path, voice_id = await _voice_asset(run, parsed_args.text)
    async with SESSION_LOCAL() as db:
        try:
            moment = await create_user_moment(
                db,
                run.user_id,
                title=parsed_args.title,
                body=parsed_args.body,
                media_url=audio_path,
                media_type="audio",
                media_metadata={"voice_id": voice_id, "transcript": parsed_args.text},
                kind=MomentKind.EMOTION.value,
                source=MomentSource.NIGHTLY.value,
            )
        except Exception:
            await _discard_assets(audio_path)
            raise
    return ActionExecutionResult(
        status="succeeded",
        moment_id=moment.id,
        fact=_fact(run, "media_voice", title=parsed_args.title),
    )


async def _execute_moment_create(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = MomentCreateArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    async with SESSION_LOCAL() as db:
        moment = await create_user_moment(
            db,
            run.user_id,
            title=parsed_args.title,
            body=parsed_args.body,
            emotion=parsed_args.emotion or None,
            kind=MomentKind.EMOTION.value,
            source=MomentSource.NIGHTLY.value,
        )
    return ActionExecutionResult(
        status="succeeded",
        moment_id=moment.id,
        fact=_fact(run, "moment_text", title=parsed_args.title),
    )


def _near_term_cron(now: datetime) -> str:
    target = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    return f"{target.minute} {target.hour} {target.day} {target.month} *"


async def _execute_outreach_schedule(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed_args = OutreachScheduleArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    execution_context = json.dumps(
        {
            "completed_nightly_action_facts": run.facts,
            "nightly_theme": run.context.plan_theme,
        },
        ensure_ascii=False,
    )
    prompt = OUTREACH_CONTEXT_TEMPLATES[resolve_language(run.context.language)].format(
        prompt=parsed_args.prompt,
        context=execution_context,
    )
    scope = MemoryScope(run.user_id, "companion")
    timezone = ZoneInfo(run.date_context.user_timezone)
    target = date.fromisoformat(run.date_context.tomorrow_date)
    # 最晚保留到用户本地次日结束。
    expires_at = datetime.combine(target + timedelta(days=1), time.min, timezone).astimezone(UTC)

    async def schedule_on_target_day(schedule: str) -> dict[str, Any] | None:
        job = await create_job(
            scope=scope,
            prompt=prompt,
            schedule=schedule,
            name=parsed_args.name or "主动问候",
            one_shot=True,
            kind="special",
            expires_at=expires_at,
        )
        next_run = job.get("next_run_at")
        if (
            not job["is_paused"]
            and isinstance(next_run, str)
            and datetime.fromisoformat(next_run).astimezone(timezone).date() == target
        ):
            return job
        await remove_job(scope, job["id"])
        return None

    now = utc_now()
    if parsed_args.local_time is not None:
        hour, minute = (int(part) for part in parsed_args.local_time.split(":"))
        planned = datetime.combine(target, time(hour, minute), timezone).astimezone(UTC)
        # 恢复执行时原定时刻可能已过，改为尽快开始等待；其余情况按规划时刻。
        schedule = (
            _near_term_cron(now)
            if planned <= now
            else f"{planned.minute} {planned.hour} {planned.day} {planned.month} *"
        )
        job = await schedule_on_target_day(schedule)
    else:
        job = await schedule_on_target_day(parsed_args.schedule or "")
        if job is None and now.astimezone(timezone).date() == target and now < expires_at:
            job = await schedule_on_target_day(_near_term_cron(now))
    if job is None:
        return ActionExecutionResult(status="failed", reason="outreach does not run on target local date")
    return ActionExecutionResult(
        status="succeeded",
        cron_job_id=job["id"],
        expires_at=job.get("expires_at"),
    )


async def _execute_action_design(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    """夜间动作设计：受理提案 → 独立评审 → approve 后入队生成。事实只叙述受理或重试，不将异步制作写成完成。"""
    documented = _CAPABILITY_BY_NAME["action.design"].arguments
    try:
        # 只取能力说明列出的参数，其余字段与其他能力一样忽略。
        request = ActionDesignArgs.model_validate({key: value for key, value in args.items() if key in documented})
    except ValidationError as exc:
        return _invalid_arguments(exc)
    name = request.name

    # 提案绑定执行时启用的动作包；没有可用包时 accept_proposal 拒绝受理。
    async with SESSION_LOCAL() as db:
        acceptance = await accept_proposal(db, run.user_id, request, source="autonomous")
        result = acceptance.result
        if result.outcome == "reused":
            await db.commit()
            return ActionExecutionResult(status="succeeded", fact=_fact(run, "action_reused", name=name))
        if result.outcome != "pending_review":
            return ActionExecutionResult(
                status="failed",
                reason=result.message or "提案未受理",
            )
        await db.commit()

    # 评审或重做异步执行；结论经 proposal 状态回流，夜间事实只叙述受理时的实际状态。
    schedule_accepted_proposal(acceptance, run.user_id)
    if result.proposal_id is not None:
        fact = _fact(run, "action_proposed", name=name)
    elif acceptance.existing_action == "in_production":
        fact = _fact(run, "action_in_production", name=name)
    elif acceptance.existing_action == "awaiting_review":
        fact = _fact(run, "action_awaiting_review", name=name)
    else:
        fact = _fact(run, "action_redo", name=name)
    return ActionExecutionResult(status="succeeded", fact=fact)


CapabilityExecutor = Callable[[_ActionRun, dict[str, Any]], Awaitable[ActionExecutionResult]]

_EXECUTORS: dict[str, CapabilityExecutor] = {
    "outfit.wear": _execute_outfit_wear,
    "outfit.create": _execute_outfit_create,
    "scene.create": _execute_scene_create,
    "scene.activate": _execute_scene_activate,
    "moment.create": _execute_moment_create,
    "media.image": _execute_media_image,
    "media.video": _execute_media_video,
    "media.voice": _execute_media_voice,
    "outreach.schedule": _execute_outreach_schedule,
    "action.design": _execute_action_design,
}


async def _execute_persisted_action(
    row: NightlyActivityAction,
    by_key: dict[str, NightlyActivityAction],
    user_id: int,
    context: PlanningContext,
    facts: list[str],
    date_context: DateContext,
) -> ActionExecutionResult:
    arguments = row.arguments or {}
    if illegal_deps := arguments.get("illegal_outfit_deps"):
        return ActionExecutionResult(
            status="failed",
            reason="场景穿着不随换装改变，不能依赖换装动作表达场景穿着；把完整造型写入 outfit_description",
            dependencies=illegal_deps,
        )
    # 依赖要求前置动作整项成功；部分成功不解锁。
    unsatisfied = [
        dep for dep in arguments.get("depends_on", []) if dep not in by_key or by_key[dep].status != "succeeded"
    ]
    if unsatisfied:
        return ActionExecutionResult(status="skipped", reason="dependency not completed", dependencies=unsatisfied)
    resume = row.result or {}
    run = _ActionRun(
        user_id=user_id,
        row_id=row.id,
        resume=resume,
        context=context,
        date_context=date_context,
        facts=facts,
    )
    # 执行前复核与状态写入同样逐项隔离：一项失败只记入该项，不中断后续动作。
    try:
        if blocked_reason := await _runtime_block_reason(user_id, row.capability, resume):
            return ActionExecutionResult(status="blocked", reason=blocked_reason)
        await _set_action_state(row.id, "running")
        return await _EXECUTORS[row.capability](run, arguments.get("values", {}))
    except Exception as exc:
        logger.warning(
            "nightly capability action failed",
            extra={"user_id": user_id, "action": row.action_key, "capability": row.capability, "error": str(exc)},
            exc_info=True,
        )
        return ActionExecutionResult(status="failed", reason=str(exc))


def _terminal_result(row: NightlyActivityAction) -> ActionExecutionResult:
    return ActionExecutionResult.model_validate(
        {"capability": row.capability, **(row.result or {"status": row.status})},
    )


async def load_terminal_action_results(log_id: int) -> dict[str, ActionExecutionResult]:
    """读取账本中已落终态的动作结果；规划阶段中途失败时，已完成动作的事实仍可进入当晚叙事。"""
    async with SESSION_LOCAL() as db:
        rows = (
            await db.scalars(
                select(NightlyActivityAction)
                .where(
                    NightlyActivityAction.log_id == log_id,
                    NightlyActivityAction.status.in_(_TERMINAL_ACTION_STATUSES),
                )
                .order_by(NightlyActivityAction.phase, NightlyActivityAction.id),
            )
        ).all()
    return {row.action_key: _terminal_result(row) for row in rows}


async def _execute_persisted_actions(
    log_id: int,
    user_id: int,
    context: PlanningContext,
    date_context: DateContext,
) -> dict[str, ActionExecutionResult]:
    rows = await _load_action_rows(log_id)
    by_key = {row.action_key: row for row in rows}
    facts = [
        str(row.result["fact"])
        for row in rows
        if row.status in _SUCCESS_ACTION_STATUSES and row.result and row.result.get("fact")
    ]
    results: dict[str, ActionExecutionResult] = {}
    for row in rows:
        if row.status in _TERMINAL_ACTION_STATUSES:
            result = _terminal_result(row)
        else:
            result = await _execute_persisted_action(row, by_key, user_id, context, facts, date_context)
            result.capability = row.capability
            # 终态统一在此落账；执行器只在付费子任务提交后写进度。
            row.status = result.status
            row.result = result.model_dump(exclude_none=True)
            try:
                await _set_action_state(row.id, row.status, row.result)
            except Exception:
                # 动作已经执行，结果仍计入本次执行；该行保留执行前状态，恢复时按进程中断的规则收敛。
                logger.exception(
                    "nightly action state write failed",
                    extra={"user_id": user_id, "action": row.action_key, "status": row.status},
                )
            if result.status in _SUCCESS_ACTION_STATUSES and result.fact:
                facts.append(result.fact)
        results[row.action_key] = result
    return results


async def run_nightly_planning(
    llm_cfg: UserLlmConfig,
    user_id: int,
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    user_profile: dict[str, str],
    recall_highlights: list[dict[str, Any]],
    date_context: DateContext,
    anomaly_stats: dict[str, Any],
    today_conversations: list[dict[str, str]],
    moment_interactions: list[dict[str, Any]],
    *,
    log_id: int,
) -> PlanningResult:
    context = await _collect_context(user_id, ZoneInfo(date_context.user_timezone))
    plan = await _stored_plan(log_id)
    if plan is None:
        payload = {
            "plan_limits": {
                "max_actions": _MAX_ACTIONS,
                "max_media_actions": _MAX_MEDIA_ACTIONS,
                "max_paid_actions": _MAX_PAID_ACTIONS,
            },
            "contextual_memories": contextual_memories,
            "background_memories_state": background_memories,
            "user_profile": user_profile,
            "recall_highlights": recall_highlights,
            "today_conversations": today_conversations,
            **({"moment_interactions": moment_interactions} if moment_interactions else {}),
            "autonomous_context": context.model_dump(exclude_none=True),
            **date_context.model_dump(),
            **anomaly_stats,
        }
        raw = await call_llm_once(
            llm_cfg,
            PLANNING_SYSTEM_PROMPT,
            payload,
            max_output_tokens=SETTINGS.nightly_planning_max_tokens,
            json_output=True,
            reasoning_effort=NIGHTLY_PLANNING_REASONING_EFFORT,
        )
        plan = _normalize_plan(parse_llm_json(raw), context)
        await _persist_plan(log_id, plan)
    context.plan_theme = plan.theme
    actions = await _execute_persisted_actions(log_id, user_id, context, date_context)
    return PlanningResult(
        theme=plan.theme,
        rationale=plan.rationale,
        actions=actions,
    )
