"""夜间 Stage 3：能力目录驱动的自主规划、持久化动作账本与顺序执行。"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    NIGHTLY_PLANNING_REASONING_EFFORT,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    safe_json_loads,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    CompanionMoment,
    CompanionOutfit,
    CompanionScene,
    Persona,
    SceneOrigin,
    SceneStatus,
)
from modules.companion.schemas_actions import ActionDesignRequest
from modules.media import VideoGenJob
from modules.scheduler import NightlyActivityAction, NightlyActivityLog
from modules.settings import UserSetting
from prompts.generation import (
    NIGHTLY_SELF_VIDEO_REFERENCE_TEMPLATE,
    SELF_IMAGE_CURRENT_OUTFIT,
    SELF_IMAGE_OUTFIT_DESCRIPTION,
    SELF_IMAGE_OUTFIT_REFERENCE,
    SELF_IMAGE_REFERENCE_TEMPLATE,
)
from prompts.nightly import OUTREACH_CONTEXT_TEMPLATE, PLANNING_SYSTEM_PROMPT
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.actions import (
    accept_proposal,
    build_action_context,
    schedule_action_generation,
    schedule_proposal_review,
)
from services.application.generation import (
    activate_outfit,
    activate_scene,
    apply_outfit_override,
    confirm_outfit,
    create_outfit_draft,
    enqueue_video_job,
    generate_images,
    load_self_visual_context,
    prepare_self_video_reference,
    resolve_image_gen_chain,
    resume_scene_generation,
    schedule_scene_generation,
)
from services.contracts import MemoryScope
from services.domains.actions.repository import get_action
from services.domains.automation import create_job, remove_job
from services.domains.companion import (
    get_scene_state,
    load_character_snapshot,
    render_character_identity,
    render_character_profile,
    scene_environment,
)
from services.domains.journal import create_generated_moment, create_user_moment
from services.infrastructure.assets import save_companion_asset
from services.infrastructure.llm import call_llm_once, resolve_provider_chain, synthesize_speech

logger = get_logger(__name__)

_SCENE_WAIT_SECONDS = 15 * 60
_POLL_SECONDS = 3.0
_IMAGE_SIZES = frozenset(
    (
        "1024x1024",
        "1024x1792",
        "1792x1024",
        "1:1",
        "16:9",
        "4:3",
        "3:2",
        "2:3",
        "3:4",
        "9:16",
        "21:9",
    ),
)
_VIDEO_ASPECT_RATIOS = frozenset(("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"))
_TERMINAL_ACTION_STATUSES = frozenset(
    ("succeeded", "partial", "skipped", "blocked", "failed", "interrupted"),
)
_SUCCESS_ACTION_STATUSES = frozenset(("succeeded", "partial"))
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

    def __init__(
        self,
        name: str,
        phase: int,
        description: str,
        arguments: dict[str, Any],
        exclusive_group: str | None = None,
        paid: bool = False,
    ) -> None:
        super().__init__(
            name=name,
            phase=phase,
            description=description,
            arguments=arguments,
            exclusive_group=exclusive_group,
            paid=paid,
        )


class DateContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_date: str
    tomorrow_date: str
    tomorrow_weekday: str
    next_7_days: list[str] = Field(default_factory=list)
    user_timezone: str


class OutfitWearArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outfit_id: int | str
    reason: str = ""


class OutfitCreateArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str
    reason: str = ""


class SceneCreateArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    notes: str = Field(min_length=1)
    outfit_description: str | None = None
    reason: str = ""


def _non_blank(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


class MomentCreateArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    body: str
    emotion: str | None = None
    reason: str = ""


class MediaImageArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str
    title: str
    body: str = ""
    size: str = "1024x1024"
    depicts_self: bool = False
    narration: str | None = None
    reason: str = ""


class MediaVideoArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str
    title: str
    body: str = ""
    duration: int | str = 6
    aspect_ratio: str = "16:9"
    depicts_self: bool = False
    narration: str | None = None
    reason: str = ""


class MediaVoiceArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    title: str
    body: str = ""
    reason: str = ""


class OutreachScheduleArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "主动问候"
    schedule: str
    prompt: str
    reason: str = ""


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

    version: int = 2
    theme: str = ""
    rationale: str = ""
    reveal: str = ""
    actions: list[PlannedAction] = Field(default_factory=list)


class ActionExecutionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    capability: str = ""
    fact: str | None = None
    reason: str | None = None
    warning: str | None = None
    dependencies: list[str] | None = None
    outfit_id: int | None = None
    scene_id: int | None = None
    moment_id: str | None = None
    job_id: int | None = None
    cron_job_id: str | None = None
    expires_at: str | None = None
    audio_path: str | None = None
    voice_id: str | None = None


class PlanningResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    rationale: str = ""
    reveal: str = ""
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


class AvailableCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    phase: int
    description: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    exclusive_group: str | None = None
    paid: bool = False


class BlockedCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str


class PlanningContext(BaseModel):
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
    available_capabilities: list[AvailableCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    plan_theme: str = ""
    action_row_id: int | None = Field(default=None, alias="_action_row_id")
    resume_result: dict[str, Any] | None = Field(default=None, alias="_resume_result")


_CAPABILITIES: tuple[NightlyCapability, ...] = (
    NightlyCapability(
        "outfit.wear",
        10,
        "穿上衣橱中一套已就绪（status=ready）的外观。outfit_id 取 wardrobe 中实际存在的 id；已经穿着的无需重复选择。",
        {"outfit_id": "integer", "reason": "string"},
        exclusive_group="outfit",
    ),
    NightlyCapability(
        "outfit.create",
        10,
        "构思并生成一套新外观并穿上；只在现有衣橱不合适或特殊节点时使用。",
        {"description": "string", "reason": "string"},
        exclusive_group="outfit",
        paid=True,
    ),
    NightlyCapability(
        "scene.activate",
        20,
        "启用 scene.library 中适合的已有场景，不消耗生图额度。场景与衣柜相互独立：不通过依赖换装动作表达场景穿着，"
        "需要特定造型时把完整设计写入所建场景的 outfit_description。",
        {"scene_id": "integer", "reason": "string"},
        exclusive_group="scene",
    ),
    NightlyCapability(
        "scene.create",
        20,
        "已有场景不适合时创建并启用新场景。notes 描述地点、环境与活动，必须非空；"
        "outfit_description 填写本次明确的完整着装设计，无着装要求时省略。"
        "场景与衣柜相互独立，不通过依赖换装动作表达场景穿着。",
        {
            "notes": "string (non-blank)",
            "outfit_description": "string (optional)",
            "reason": "string",
        },
        exclusive_group="scene",
        paid=True,
    ),
    NightlyCapability(
        "moment.create",
        30,
        "写下一条文字片刻，例如一张便笺、愿望或值得纪念的小事。",
        {"title": "string", "body": "string", "emotion": "string"},
        exclusive_group="text_moment",
    ),
    NightlyCapability(
        "media.image",
        30,
        "创作保存到片刻的图片；depicts_self=true 时使用角色身份与衣柜已启用外观描述。narration 是可选的独立语音，不会让图片中的人物活动。",
        {
            "prompt": "string",
            "title": "string",
            "body": "string",
            "size": sorted(_IMAGE_SIZES),
            "depicts_self": "boolean",
            "narration": "optional string",
            "reason": "string",
        },
        paid=True,
    ),
    NightlyCapability(
        "media.video",
        30,
        "创作保存到片刻的短视频；depicts_self=true 时使用符合角色固定外形与衣柜已启用外观的首帧并保持其穿着。"
        "仅当确实要展示衣柜中新换的外观时才依赖对应换装动作，场景穿着不构成这种依赖。narration 是使用当前音色生成的独立音轨，不保证口型同步。",
        {
            "prompt": "string",
            "title": "string",
            "body": "string",
            "duration": "6|10",
            "aspect_ratio": sorted(_VIDEO_ASPECT_RATIOS),
            "depicts_self": "boolean",
            "narration": "optional string",
            "reason": "string",
        },
        paid=True,
    ),
    NightlyCapability(
        "media.voice",
        30,
        "用角色当前音色录制一段语音心意并永久保存到片刻。",
        {"text": "string", "title": "string", "body": "string", "reason": "string"},
        paid=True,
    ),
    NightlyCapability(
        "outreach.schedule",
        40,
        "安排次日主动联系；从计划时间起等待用户在线，最晚保留到用户本地次日结束。",
        {"name": "string", "schedule": "five-field UTC cron", "prompt": "string"},
        exclusive_group="outreach",
    ),
    NightlyCapability(
        "action.design",
        25,
        "为当前形象提交一个可反复使用的新动作提案（如张开双臂、打哈欠、一段舞蹈），不是一次性视频作品。"
        "先查看 autonomous_context.actions.library 的动作内容和适用条件，以及同处的 in_flight_proposals、recent_rejections；"
        "提案的 design 是原设计，reason 是评审理由，status 与 action_status 分别说明评审和制作进展。"
        "确有缺口才使用；无明确价值时选择不使用。"
        "name 是动作显示名称；motion_description 写单主体可见的姿态、节奏与神态，"
        "不含场景、镜头或产品概念；use_when / avoid_when 说明何时适用或避免；reason 说明为何需要新动作。"
        "duration_seconds 为 1–10 的整秒数；clip_kind 为 loop（连续运动周期）或 once（完整动作自然收束）。"
        "once 制作后仍可重复使用。单主体原地运动、固定镜头、全身入画，保持身体结构与穿着，"
        "不新增人物、道具、场景、对话或音轨。依赖换装时把 depends_on 填为对应换装动作，"
        "列表只描述规划时的形象，不能据此断定换装后的动作缺口；素材属于执行时启用的形象，不能跨形象复用。"
        "受理仅表示申请成功，独立评审和制作随后进行；"
        "后续片刻或联系不能以依赖此项为依据宣称动作已做好或已表演。",
        {
            "name": "string, 1-64 characters",
            "motion_description": "string, 10-600 characters",
            "use_when": "optional list[string], at most 8 items, each at most 120 characters",
            "avoid_when": "optional list[string], at most 8 items, each at most 120 characters",
            "reason": "string, 1-400 characters",
            "duration_seconds": "integer 1-10",
            "clip_kind": "loop|once",
        },
        paid=True,
    ),
)
_CAPABILITY_BY_NAME = {cap.name: cap for cap in _CAPABILITIES}


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _setting_value(rows: dict[str, str], key: str, default: Any) -> Any:
    raw = rows.get(key)
    if raw is None:
        return default
    return safe_json_loads(raw, default=default)


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
        chain, _ = await resolve_image_gen_chain(db, user_id, "nightly-reference")
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
) -> tuple[list[AvailableCapability], list[BlockedCapability]]:
    policy = context.policies
    providers = context.providers
    wardrobe = context.wardrobe
    persona_ready = bool(context.persona.complete)
    has_ready_outfit = any(item.status == "ready" for item in wardrobe)
    rules: dict[str, tuple[bool, str]] = {
        "outfit.wear": (
            policy.outfit != "locked" and has_ready_outfit,
            "换装已锁定或没有 ready 外观",
        ),
        "outfit.create": (
            policy.outfit != "locked" and providers.image_reference and persona_ready,
            "换装已锁定或形象/生图不可用",
        ),
        "scene.activate": (policy.scene != "locked" and bool(context.scene.library), "场景已锁定或没有可用场景"),
        "scene.create": (
            policy.scene != "locked"
            and providers.image_reference
            and persona_ready
            and not context.scene.generation_pending,
            "场景已锁定、形象/生图不可用或已有场景正在生成",
        ),
        "moment.create": (True, ""),
        "media.image": (
            bool(policy.media) and providers.image,
            "自主心意或生图供应商不可用",
        ),
        "media.video": (
            bool(policy.media) and providers.video,
            "自主心意或视频供应商不可用",
        ),
        "media.voice": (
            bool(policy.voice) and providers.tts,
            "夜间自主语音或 TTS 供应商不可用",
        ),
        "outreach.schedule": (True, ""),
        "action.design": (
            bool(context.actions.get("pack_id")) and providers.video,
            "没有就绪的外观动作包或视频供应商不可用",
        ),
    }
    available: list[AvailableCapability] = []
    blocked: list[BlockedCapability] = []
    for capability in _CAPABILITIES:
        allowed, reason = rules[capability.name]
        if allowed:
            available.append(
                AvailableCapability(
                    name=capability.name,
                    phase=capability.phase,
                    description=capability.description,
                    arguments=dict(capability.arguments),
                    exclusive_group=capability.exclusive_group,
                    paid=capability.paid,
                ),
            )
        else:
            blocked.append(BlockedCapability(name=capability.name, reason=reason))
    return available, blocked


async def _planning_action_context(db: Any, user_id: int) -> dict[str, Any]:
    """动作库摘要：当前包就绪动作、在途提案与近期拒绝，供缺口评估。"""
    snapshot = await build_action_context(db, user_id)
    return {
        "pack_id": snapshot.pack_id,
        "catalog_version": snapshot.catalog_version,
        "library": snapshot.ready_actions,
        "in_flight_proposals": snapshot.in_flight_proposals,
        "recent_rejections": snapshot.recent_rejections,
    }


async def _collect_context(user_id: int) -> PlanningContext:
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        outfits = (
            (
                await db.execute(
                    select(CompanionOutfit)
                    .where(CompanionOutfit.user_id == user_id)
                    .order_by(CompanionOutfit.created_at.desc()),
                )
            )
            .scalars()
            .all()
        )
        scene = await get_scene_state(db, user_id)
        scene_rows = list(
            (
                await db.scalars(
                    select(CompanionScene)
                    .where(
                        CompanionScene.user_id == user_id,
                        CompanionScene.status == SceneStatus.READY.value,
                    )
                    .order_by(CompanionScene.id.desc()),
                )
            ).all(),
        )
        character = await load_character_snapshot(db, user_id)
        setting_rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                ),
            )
        ).all()
        settings = {str(key): str(value) for key, value in setting_rows}
        image_available = await _provider_available(db, user_id, "image_gen")
        reference_image_available = await _reference_image_provider_available(db, user_id)
        video_available = await _provider_available(db, user_id, "video_gen")
        tts_available = await _provider_available(db, user_id, "tts")
        recent_actions = (
            (
                await db.execute(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.user_id == user_id)
                    .order_by(NightlyActivityAction.id.desc())
                    .limit(40),
                )
            )
            .scalars()
            .all()
        )
        recent_moments = (
            (
                await db.execute(
                    select(CompanionMoment)
                    .where(CompanionMoment.user_id == user_id)
                    .order_by(CompanionMoment.occurred_at.desc())
                    .limit(20),
                )
            )
            .scalars()
            .all()
        )
        # 动作库摘要在会话生命周期内读取，避免 session 关闭后重开未托管事务。
        action_snapshot = await _planning_action_context(db, user_id)

    definition = safe_json_loads(
        persona.definition_json if persona is not None else "{}",
        default={},
    )
    if not isinstance(definition, dict):
        definition = {}
    definition.pop("appearance", None)
    if character is not None:
        definition["fixed_features"] = render_character_profile(character)
    context = PlanningContext(
        policies=PlanningPolicies(
            outfit=persona.outfit_policy if persona is not None else "llm_may_replace",
            scene=scene.policy,
            media=_setting_value(settings, "companion.autonomous_media", True) is True,
            voice=_setting_value(settings, "companion.autonomous_voice", True) is True,
        ),
        providers=PlanningProviders(
            image=image_available,
            image_reference=reference_image_available,
            video=video_available,
            tts=tts_available,
        ),
        selected_voice_id=str(
            _setting_value(settings, "companion.voice_id", "") or "",
        ),
        language=str(_setting_value(settings, "language", "") or ""),
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
        actions=action_snapshot,
        recent_autonomous_actions=[
            RecentActionSummary(
                date=row.target_date.isoformat(),
                capability=row.capability,
                status=row.status,
                fact=(row.result or {}).get("fact"),
            )
            for row in recent_actions
        ],
        recent_moments=[
            RecentMomentSummary(
                date=row.occurred_at.date().isoformat(),
                kind=row.kind,
                title=row.title,
                source=row.source,
            )
            for row in recent_moments
        ],
    )
    available, blocked = _capability_availability(context)
    context.available_capabilities = available
    context.blocked_capabilities = blocked
    return context


def _normalize_action_id(raw: Any, index: int, seen: set[str]) -> str:
    candidate = _ACTION_ID_PATTERN.sub("_", _text(raw, 48)).strip("_")
    if not candidate:
        candidate = f"action_{index + 1}"
    base = candidate
    suffix = 2
    while candidate in seen:
        candidate = f"{base}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _normalize_plan(parsed: Any, context: PlanningContext) -> NormalizedPlan:
    if not isinstance(parsed, dict):
        raise TypeError("nightly planning returned invalid JSON")
    available_names = {item.name for item in context.available_capabilities}
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = []
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    media_count = 0
    paid_count = 0
    actions: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_actions):
        if len(actions) >= _MAX_ACTIONS:
            break
        if not isinstance(raw, dict):
            continue
        capability_name = _text(raw.get("capability"), 64)
        spec = _CAPABILITY_BY_NAME.get(capability_name)
        if spec is None or capability_name not in available_names:
            continue
        if spec.exclusive_group and spec.exclusive_group in seen_groups:
            continue
        if spec.paid and paid_count >= _MAX_PAID_ACTIONS:
            continue
        if capability_name.startswith("media."):
            if media_count >= _MAX_MEDIA_ACTIONS:
                continue
            media_count += 1
        action_id = _normalize_action_id(raw.get("id"), index, seen_ids)
        dependencies = [
            _ACTION_ID_PATTERN.sub("_", str(item))[:48] for item in raw.get("depends_on", []) if isinstance(item, str)
        ]
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        if (
            capability_name == "media.image"
            and args.get("depicts_self") is True
            and not context.providers.image_reference
        ):
            continue
        actions.append(
            {
                "id": action_id,
                "capability": capability_name,
                "phase": spec.phase,
                "depends_on": dependencies,
                "arguments": args,
                "_order": index,
            },
        )
        if spec.paid:
            paid_count += 1
        if spec.exclusive_group:
            seen_groups.add(spec.exclusive_group)
    valid_ids = {item["id"] for item in actions}
    outfit_ids = {item["id"] for item in actions if item["capability"].startswith("outfit.")}
    for action in actions:
        if action["capability"].startswith("scene."):
            illegal = [dep for dep in action["depends_on"] if dep in outfit_ids]
            if illegal:
                action["_invalid_scene_outfit_deps"] = illegal
        action["depends_on"] = [dep for dep in action["depends_on"] if dep in valid_ids and dep != action["id"]]
    actions.sort(key=lambda item: (item["phase"], item["_order"]))
    planned_actions = [
        PlannedAction(
            id=item["id"],
            capability=item["capability"],
            phase=item["phase"],
            depends_on=item["depends_on"],
            arguments=item["arguments"],
            illegal_outfit_deps=item.get("_invalid_scene_outfit_deps", []),
        )
        for item in actions
    ]
    return NormalizedPlan(
        version=2,
        theme=_text(parsed.get("theme"), 200),
        rationale=_text(parsed.get("rationale"), 1000),
        reveal=_text(parsed.get("reveal"), 500),
        actions=planned_actions,
    )


async def _stored_plan(log_id: int | None) -> NormalizedPlan | None:
    if log_id is None:
        return None
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
        payload = log.payload if log is not None and isinstance(log.payload, dict) else {}
        plan = payload.get("nightly_plan")
        if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
            try:
                return NormalizedPlan.model_validate(plan)
            except ValidationError:
                return None
        return None


async def _persist_plan(
    log_id: int | None,
    user_id: int,
    target_date: date,
    plan: NormalizedPlan,
) -> None:
    if log_id is None:
        return
    async with SESSION_LOCAL() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log is None:
            return
        payload = dict(log.payload) if isinstance(log.payload, dict) else {}
        payload["nightly_plan"] = plan.model_dump()
        log.payload = payload
        existing_keys = set(
            (
                await db.execute(
                    select(NightlyActivityAction.action_key).where(
                        NightlyActivityAction.log_id == log_id,
                    ),
                )
            )
            .scalars()
            .all(),
        )
        for action in plan.actions:
            if action.id in existing_keys:
                continue
            db.add(
                NightlyActivityAction(
                    log_id=log_id,
                    user_id=user_id,
                    target_date=target_date,
                    action_key=action.id,
                    capability=action.capability,
                    phase=action.phase,
                    arguments={
                        "depends_on": action.depends_on,
                        "values": action.arguments,
                        **({"illegal_outfit_deps": action.illegal_outfit_deps} if action.illegal_outfit_deps else {}),
                    },
                ),
            )
        await db.commit()


async def _load_action_rows(log_id: int) -> list[NightlyActivityAction]:
    async with SESSION_LOCAL() as db:
        running = (
            (
                await db.execute(
                    select(NightlyActivityAction).where(
                        NightlyActivityAction.log_id == log_id,
                        NightlyActivityAction.status == "running",
                    ),
                )
            )
            .scalars()
            .all()
        )
        for row in running:
            progress_key = {
                "outfit.create": "outfit_id",
                "scene.create": "scene_id",
                "media.video": "job_id",
            }.get(row.capability)
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
        return list(
            (
                await db.execute(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.log_id == log_id)
                    .order_by(NightlyActivityAction.phase, NightlyActivityAction.id),
                )
            )
            .scalars()
            .all(),
        )


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


async def _record_executor_state(
    context: PlanningContext,
    status: str,
    result: ActionExecutionResult | dict[str, Any],
) -> None:
    """副作用一落地就写动作账本，缩小执行器返回前进程退出造成的重复窗口。"""
    action_row_id = context.action_row_id
    if isinstance(action_row_id, int):
        data = result.model_dump(exclude_none=True) if isinstance(result, BaseModel) else result
        await _set_action_state(action_row_id, status, data)


async def _wait_for_outfit(user_id: int, outfit_id: int) -> CompanionOutfit | None:
    """确认后的外观读取：确认是同步操作，这里只兜底核对最终状态。"""
    async with SESSION_LOCAL() as db:
        return (
            await db.execute(
                select(CompanionOutfit).where(
                    CompanionOutfit.user_id == user_id,
                    CompanionOutfit.id == outfit_id,
                ),
            )
        ).scalar_one_or_none()


async def _outfit_policy_allows(user_id: int) -> bool:
    async with SESSION_LOCAL() as db:
        policy = await db.scalar(
            select(Persona.outfit_policy).where(Persona.user_id == user_id),
        )
    return policy != "locked"


async def _execute_outfit_wear(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _outfit_policy_allows(user_id):
        return ActionExecutionResult(status="blocked", reason="outfit policy locked")
    try:
        parsed_args = OutfitWearArgs.model_validate(args)
        outfit_id = int(parsed_args.outfit_id)
    except (ValidationError, TypeError, ValueError):
        return ActionExecutionResult(status="failed", reason="invalid outfit_id")
    listed_ready_ids = {int(item.id) for item in context.wardrobe if item.status == "ready"}
    if outfit_id not in listed_ready_ids:
        return ActionExecutionResult(status="failed", reason="outfit was not a listed ready item")
    if any(int(item.id) == outfit_id and item.active is True for item in context.wardrobe):
        return ActionExecutionResult(status="skipped", reason="outfit is already active")
    async with SESSION_LOCAL() as db:
        outfit = await activate_outfit(db, user_id, outfit_id)
    result = ActionExecutionResult(
        status="succeeded",
        outfit_id=outfit.id,
        fact=f"换上了已有外观「{outfit.name}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_outfit_create(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _outfit_policy_allows(user_id):
        return ActionExecutionResult(status="blocked", reason="outfit policy locked")
    try:
        parsed_args = OutfitCreateArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing outfit description")
    description = _text(parsed_args.description, 500)
    if not description:
        return ActionExecutionResult(status="failed", reason="missing outfit description")
    resume_result = context.resume_result
    resume_outfit_id = resume_result.get("outfit_id") if isinstance(resume_result, dict) else None
    try:
        outfit_id = int(resume_outfit_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        async with SESSION_LOCAL() as db:
            draft = await create_outfit_draft(db, user_id, description=description)
            confirmed = await confirm_outfit(db, user_id, draft.id)
        outfit_id = confirmed.id
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(status="running", outfit_id=outfit_id),
        )
    ready = await _wait_for_outfit(user_id, outfit_id)
    # 确认是同步的：缺失或未就绪说明外观已被删除 / 重绘回草稿，不能盲目重做付费生成。
    if ready is None or ready.status != "ready":
        return ActionExecutionResult(
            status="failed",
            outfit_id=outfit_id,
            reason="outfit is not ready after confirm",
        )
    if not ready.active:
        async with SESSION_LOCAL() as db:
            ready = await activate_outfit(db, user_id, ready.id, require_current_identity=True)
    display_name = ready.name if ready.name != "新外观" else description[:40]
    result = ActionExecutionResult(
        status="succeeded",
        outfit_id=ready.id,
        fact=f"设计并换上了新外观「{display_name}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _wait_for_scene(
    user_id: int,
    scene_id: int,
) -> CompanionScene | None:
    deadline = monotonic() + _SCENE_WAIT_SECONDS
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(CompanionScene).where(
                        CompanionScene.user_id == user_id,
                        CompanionScene.id == scene_id,
                    ),
                )
            ).scalar_one_or_none()
            if row is None or row.status in (
                SceneStatus.FAILED.value,
                SceneStatus.CANCELLED.value,
                SceneStatus.DESCRIPTION_FAILED.value,
            ):
                return None
            if row.status == SceneStatus.READY.value:
                return row if row.activated_at is not None else None
        await asyncio.sleep(_POLL_SECONDS)
    return None


async def _execute_scene_activate(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    async with SESSION_LOCAL() as db:
        row = await activate_scene(db, user_id, int(args["scene_id"]), origin=SceneOrigin.NIGHTLY.value)
    result = ActionExecutionResult(
        status="succeeded",
        scene_id=row.id,
        fact=f"当前所在的场景变为「{row.title}」：{row.description}",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_scene_create(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    try:
        parsed_args = SceneCreateArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="scene.create 需要非空的 notes 文本")
    notes = _non_blank(parsed_args.notes)
    outfit_description = _non_blank(parsed_args.outfit_description)
    if not notes:
        return ActionExecutionResult(status="failed", reason="scene.create notes 不能为空白")
    resume_result = context.resume_result or {}
    scene_id = resume_result.get("scene_id")
    if scene_id is None:
        row = await schedule_scene_generation(
            user_id,
            origin=SceneOrigin.NIGHTLY.value,
            notes=notes,
            outfit_description=outfit_description or None,
            auto_activate=True,
        )
        scene_id = row.id
        await _record_executor_state(context, "running", ActionExecutionResult(status="running", scene_id=scene_id))
    else:
        scene_id = int(scene_id)
        await resume_scene_generation(user_id, scene_id)
    ready = await _wait_for_scene(user_id, scene_id)
    if ready is None:
        return ActionExecutionResult(
            status="interrupted",
            scene_id=scene_id,
            reason="场景尚未确认启用，不能记录到达事实；可查询原任务",
        )
    result = ActionExecutionResult(
        status="succeeded",
        scene_id=ready.id,
        fact=f"当前所在的场景变为「{ready.title}」：{ready.description}",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _wait_for_video(user_id: int, job_id: int) -> VideoGenJob | None:
    deadline = monotonic() + float(SETTINGS.video_gen_max_poll_seconds) + 30
    while monotonic() < deadline:
        async with SESSION_LOCAL() as db:
            row = (
                await db.execute(
                    select(VideoGenJob).where(
                        VideoGenJob.user_id == user_id,
                        VideoGenJob.id == job_id,
                    ),
                )
            ).scalar_one_or_none()
            if row is None or row.status in ("failed", "result_unknown"):
                return None
            if row.status == "succeeded":
                return row
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


async def _voice_asset(
    user_id: int,
    text: str,
    context: PlanningContext,
) -> tuple[str, str]:
    result = await synthesize_speech(
        user_id,
        text,
        context.selected_voice_id,
        context.language,
    )
    path = save_companion_asset(
        result.audio,
        user_id=user_id,
        label="nightly_voice",
        ext=_audio_extension(result.mime),
    )
    return path, result.voice or ""


async def _runtime_media_allowed(
    user_id: int,
    *,
    include_media: bool = True,
    include_voice: bool = False,
) -> bool:
    keys: list[str] = []
    if include_media:
        keys.append("companion.autonomous_media")
    if include_voice:
        keys.append("companion.autonomous_voice")
    async with SESSION_LOCAL() as db:
        rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key.in_(keys),
                ),
            )
        ).all()
    values = {str(key): str(value) for key, value in rows}
    return all(_setting_value(values, key, True) is True for key in keys)


async def _runtime_capability_allowed(
    user_id: int,
    capability: str,
    resume_result: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """动作真正执行前重读总控与能力开关，避免长耗时计划期间用户关锁后仍产生副作用。"""
    async with SESSION_LOCAL() as db:
        user = await db.get(User, user_id)
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        scene_pending: tuple[int, str] | None = None
        if capability == "scene.create":
            scene_pending = (
                await db.execute(
                    select(
                        CompanionScene.id,
                        CompanionScene.origin,
                    )
                    .where(
                        CompanionScene.user_id == user_id,
                        CompanionScene.status == SceneStatus.PENDING.value,
                    )
                    .limit(1),
                )
            ).one_or_none()
        rows = (
            await db.execute(
                select(UserSetting.setting_key, UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key.in_(
                        (
                            "companion.autonomous_media",
                            "companion.autonomous_voice",
                        ),
                    ),
                ),
            )
        ).all()
    if user is None or not user.nightly_activity_enabled:
        return False, "nightly activity disabled"
    settings = {str(key): str(value) for key, value in rows}
    if capability.startswith("outfit.") and persona is not None and persona.outfit_policy == "locked":
        return False, "outfit policy locked"
    if capability.startswith("scene.") and persona is not None and persona.scene_policy == "locked":
        return False, "scene policy locked"
    if capability == "scene.create" and scene_pending is not None:
        resume_scene_id = None
        if isinstance(resume_result, dict):
            with suppress(TypeError, ValueError):
                resume_scene_id = int(resume_result.get("scene_id"))
        pending_id, _pending_origin = scene_pending
        if pending_id != resume_scene_id:
            return False, "another scene generation is already pending"
    if (
        capability in ("media.image", "media.video")
        and _setting_value(settings, "companion.autonomous_media", True) is not True
    ):
        return False, "autonomous media disabled"
    if capability == "media.voice" and _setting_value(settings, "companion.autonomous_voice", True) is not True:
        return False, "autonomous voice disabled"
    return True, ""


async def _optional_narration(
    user_id: int,
    narration: str | None,
    context: PlanningContext,
) -> tuple[str | None, str | None, str | None]:
    cleaned = _text(narration, 800)
    if not cleaned:
        return None, None, None
    resume_result = context.resume_result
    if isinstance(resume_result, dict) and isinstance(resume_result.get("audio_path"), str):
        return resume_result["audio_path"], str(resume_result.get("voice_id") or ""), None
    if not context.providers.tts or not await _runtime_media_allowed(
        user_id,
        include_voice=True,
    ):
        return None, None, "narration unavailable or disabled"
    try:
        audio_path, voice_id = await _voice_asset(user_id, cleaned, context)
        return audio_path, voice_id, None
    except Exception as exc:
        logger.warning(
            "nightly media narration failed",
            extra={"user_id": user_id, "error": str(exc)},
            exc_info=True,
        )
        return None, None, str(exc)


async def _execute_media_image(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(user_id):
        return ActionExecutionResult(status="blocked", reason="autonomous media disabled")
    try:
        parsed_args = MediaImageArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing image prompt or title")
    prompt = _text(parsed_args.prompt, 4000)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not prompt or not title:
        return ActionExecutionResult(status="failed", reason="missing image prompt or title")
    size = _text(parsed_args.size, 16)
    identity = outfit = None
    if parsed_args.depicts_self is True:
        visual = await load_self_visual_context(user_id)
        identity, outfit = visual.reference_image, visual.outfit_reference
        prompt = (
            SELF_IMAGE_REFERENCE_TEMPLATE.format(
                reference="图 1" if outfit else "参考图",
                outfit=SELF_IMAGE_OUTFIT_DESCRIPTION.format(outfit=visual.outfit_description)
                if visual.outfit_description
                else (SELF_IMAGE_OUTFIT_REFERENCE if outfit else SELF_IMAGE_CURRENT_OUTFIT),
                prompt=prompt,
            )
            + "\n"
            + render_character_identity(visual.identity)
        )
    urls = await generate_images(
        prompt,
        size=size if size in _IMAGE_SIZES else "1024x1024",
        n=1,
        user_id=user_id,
        reference_image=identity,
        secondary_reference_image=outfit,
    )
    audio_path, voice_id, narration_error = await _optional_narration(
        user_id,
        parsed_args.narration,
        context,
    )
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=urls[0],
            media_type="image",
            audio_url=audio_path,
            media_metadata={"voice_id": voice_id} if voice_id else None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        moment_id=str(moment.id),
        fact=f"在片刻相册里准备了图片心意「{title}」",
        warning=narration_error or None,
    )
    await _record_executor_state(context, result.status, result)
    return result


async def _execute_media_video(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(user_id):
        return ActionExecutionResult(status="blocked", reason="autonomous media disabled")
    try:
        parsed_args = MediaVideoArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing video prompt or title")
    prompt = _text(parsed_args.prompt, 4000)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not prompt or not title:
        return ActionExecutionResult(status="failed", reason="missing video prompt or title")
    try:
        duration = int(parsed_args.duration)
    except (TypeError, ValueError):
        duration = 6
    duration = duration if duration in (6, 10) else 6
    aspect_ratio = _text(parsed_args.aspect_ratio, 16)
    resume_result = context.resume_result
    resume_job_id = resume_result.get("job_id") if isinstance(resume_result, dict) else None
    try:
        job_id = int(resume_job_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        first_frame = None
        if parsed_args.depicts_self is True:
            visual = await load_self_visual_context(user_id)
            first_frame = await prepare_self_video_reference(apply_outfit_override(visual, None), user_id)
            prompt = (
                NIGHTLY_SELF_VIDEO_REFERENCE_TEMPLATE.format(prompt=prompt)
                + "\n"
                + render_character_identity(visual.identity)
            )
        async with SESSION_LOCAL() as db:
            job = await enqueue_video_job(
                db,
                user_id=user_id,
                session_id=None,
                prompt=prompt,
                duration=duration,
                resolution="768P",
                first_frame_image=first_frame,
                model=None,
                aspect_ratio=aspect_ratio if aspect_ratio in _VIDEO_ASPECT_RATIOS else "16:9",
            )
        job_id = job.id
        if job.status == "result_unknown":
            result = ActionExecutionResult(
                status="failed",
                job_id=job_id,
                reason=job.error_message,
            )
            await _record_executor_state(context, "failed", result)
            return result
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(status="running", job_id=job_id),
        )
    completed = await _wait_for_video(user_id, job_id)
    if completed is None or not completed.video_url:
        return ActionExecutionResult(
            status="failed",
            job_id=job_id,
            reason="video generation failed or timed out",
        )
    audio_path, voice_id, narration_error = await _optional_narration(
        user_id,
        parsed_args.narration,
        context,
    )
    if audio_path:
        await _record_executor_state(
            context,
            "running",
            ActionExecutionResult(
                status="running",
                job_id=job_id,
                audio_path=audio_path,
                voice_id=voice_id,
            ),
        )
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=completed.video_url,
            media_type="video",
            audio_url=audio_path,
            media_metadata={
                "voice_id": voice_id,
                "narration": _text(parsed_args.narration, 800),
            }
            if voice_id
            else None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded" if narration_error is None else "partial",
        job_id=job_id,
        moment_id=str(moment.id),
        fact=f"在片刻相册里准备了视频心意「{title}」",
        warning=narration_error or None,
    )
    await _record_executor_state(context, result.status, result)
    return result


async def _execute_media_voice(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    if not await _runtime_media_allowed(
        user_id,
        include_media=False,
        include_voice=True,
    ):
        return ActionExecutionResult(status="blocked", reason="autonomous voice disabled")
    try:
        parsed_args = MediaVoiceArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing voice text or title")
    spoken = _text(parsed_args.text, 800)
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not spoken or not title:
        return ActionExecutionResult(status="failed", reason="missing voice text or title")
    audio_path, voice_id = await _voice_asset(user_id, spoken, context)
    async with SESSION_LOCAL() as db:
        moment = await create_generated_moment(
            db,
            user_id,
            title=title,
            body=body,
            media_url=audio_path,
            media_type="audio",
            media_metadata={"voice_id": voice_id, "transcript": spoken},
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded",
        moment_id=str(moment.id),
        fact=f"在片刻相册里留下了语音心意「{title}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_moment_create(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    *_: Any,
) -> ActionExecutionResult:
    try:
        parsed_args = MomentCreateArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing moment title or body")
    title = _text(parsed_args.title, 64)
    body = _text(parsed_args.body, 500)
    if not title or not body:
        return ActionExecutionResult(status="failed", reason="missing moment title or body")
    async with SESSION_LOCAL() as db:
        moment = await create_user_moment(
            db,
            user_id,
            title=title,
            body=body,
            emotion=_text(parsed_args.emotion, 32) or None,
            kind="together",
            source="nightly",
        )
    result = ActionExecutionResult(
        status="succeeded",
        moment_id=str(moment.id),
        fact=f"在片刻里写下了「{title}」",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


def _runs_on_target_local_date(
    job: dict[str, Any],
    date_context: DateContext,
) -> bool:
    try:
        next_run = datetime.fromisoformat(str(job["next_run_at"]))
        target = date.fromisoformat(str(date_context.tomorrow_date))
        timezone = ZoneInfo(str(date_context.user_timezone))
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    return next_run.astimezone(timezone).date() == target


def _outreach_expiry(date_context: DateContext) -> datetime:
    target = date.fromisoformat(str(date_context.tomorrow_date))
    timezone = ZoneInfo(str(date_context.user_timezone))
    return datetime.combine(target + timedelta(days=1), time.min, timezone).astimezone(
        UTC,
    )


def _near_term_cron(now: datetime) -> str:
    target = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    return f"{target.minute} {target.hour} {target.day} {target.month} *"


async def _execute_outreach_schedule(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    facts: list[str],
    date_context: DateContext,
) -> ActionExecutionResult:
    async with SESSION_LOCAL() as db:
        user = await db.get(User, user_id)
    if user is None or not user.nightly_activity_enabled:
        return ActionExecutionResult(status="blocked", reason="nightly activity disabled")
    try:
        parsed_args = OutreachScheduleArgs.model_validate(args)
    except ValidationError:
        return ActionExecutionResult(status="failed", reason="missing outreach schedule or prompt")
    name = _text(parsed_args.name, 100) or "主动问候"
    schedule = _text(parsed_args.schedule, 100)
    prompt = _text(parsed_args.prompt, 4000)
    if not schedule or not prompt:
        return ActionExecutionResult(status="failed", reason="missing outreach schedule or prompt")
    execution_context = json.dumps(
        {
            "completed_nightly_action_facts": facts,
            "nightly_theme": context.plan_theme,
        },
        ensure_ascii=False,
    )
    effective_prompt = OUTREACH_CONTEXT_TEMPLATE.format(prompt=prompt, context=execution_context)
    job = await create_job(
        scope=MemoryScope(user_id, "companion"),
        prompt=effective_prompt,
        schedule=schedule,
        name=name,
        deliver="local",
        one_shot=True,
        kind="special",
        expires_at=_outreach_expiry(date_context),
    )
    if job["is_paused"] or not _runs_on_target_local_date(job, date_context):
        await remove_job(MemoryScope(user_id, "companion"), job["id"])
        timezone = ZoneInfo(str(date_context.user_timezone))
        target = date.fromisoformat(str(date_context.tomorrow_date))
        now = utc_now()
        if now.astimezone(timezone).date() != target or now >= _outreach_expiry(date_context):
            return ActionExecutionResult(
                status="failed",
                reason="outreach does not run on target local date",
            )
        job = await create_job(
            scope=MemoryScope(user_id, "companion"),
            prompt=effective_prompt,
            schedule=_near_term_cron(now),
            name=name,
            deliver="local",
            one_shot=True,
            kind="special",
            expires_at=_outreach_expiry(date_context),
        )
        if job["is_paused"] or not _runs_on_target_local_date(job, date_context):
            await remove_job(MemoryScope(user_id, "companion"), job["id"])
            return ActionExecutionResult(
                status="failed",
                reason="outreach recovery could not find a remaining target-day slot",
            )
    result = ActionExecutionResult(
        status="succeeded",
        cron_job_id=job["id"],
        expires_at=job.get("expires_at"),
        fact="为次日安排了一次主动问候",
    )
    await _record_executor_state(context, "succeeded", result)
    return result


async def _execute_action_design(
    user_id: int,
    args: dict[str, Any],
    context: PlanningContext,
    dependencies: list[str],
    date_context: DateContext,
) -> ActionExecutionResult:
    """夜间动作设计：受理提案 → 独立评审 → approve 后入队生成。

    事实只叙述受理或重试，不将异步制作写成完成。
    """
    name = _text(args.get("name"), 64)
    motion = _text(args.get("motion_description"), 600)
    reason = _text(args.get("reason"), 400)
    if not name or len(motion) < 10:
        return ActionExecutionResult(status="failed", reason="动作设计缺少名称或有效运动描述")

    # 换装依赖：夜间先换装时，动作设计须绑定换装后实际就绪的包。
    pack_ready = any(item.status == "ready" for item in context.wardrobe)
    if not pack_ready:
        return ActionExecutionResult(status="failed", reason="当前没有就绪的形象动作，无法设计新动作")

    try:
        request = ActionDesignRequest(
            name=name,
            motion_description=motion,
            use_when=[_text(v, 120) for v in (args.get("use_when") or []) if isinstance(v, str)][:8],
            avoid_when=[_text(v, 120) for v in (args.get("avoid_when") or []) if isinstance(v, str)][:8],
            reason=reason or "夜间能力评估：存在表达缺口",
            duration_seconds=float(args.get("duration_seconds") or 4),
            clip_kind=str(args.get("clip_kind") or "once"),
        )
    except Exception:
        return ActionExecutionResult(status="failed", reason="动作设计参数不合法")

    async with SESSION_LOCAL() as db:
        result = await accept_proposal(db, user_id, request, source="autonomous")
        retry_pack_id = None
        if result.outcome == "pending_review" and result.action_id is not None and result.proposal_id is None:
            action = await get_action(db, result.action_id)
            retry_pack_id = action.pack_id if action is not None else None
        if result.outcome == "reused":
            await db.commit()
            return ActionExecutionResult(
                status="succeeded",
                fact=result.message or f"已有可复用动作「{name}」，无需新建",
            )
        if result.outcome != "pending_review" or (result.proposal_id is None and retry_pack_id is None):
            return ActionExecutionResult(
                status="failed",
                reason=result.message or "提案未受理",
            )
        await db.commit()

    if result.proposal_id is not None:
        # 评审异步执行；结论经 proposal 状态回流，夜间事实只叙述「已提交制作申请」。
        schedule_proposal_review(result.proposal_id, user_id)
        return ActionExecutionResult(
            status="succeeded",
            fact=f"提交了新动作「{name}」的制作申请（等待独立评审与制作，尚未确认就绪）",
        )
    schedule_action_generation(retry_pack_id, result.action_id, user_id)
    return ActionExecutionResult(
        status="succeeded",
        fact=f"已申请重新制作动作「{name}」，尚未确认就绪",
    )


CapabilityExecutor = Callable[
    [int, dict[str, Any], PlanningContext, list[str], DateContext],
    Awaitable[ActionExecutionResult],
]

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


async def _execute_persisted_actions(
    log_id: int,
    user_id: int,
    context: PlanningContext,
    date_context: DateContext,
) -> dict[str, ActionExecutionResult]:
    rows = await _load_action_rows(log_id)
    by_key = {row.action_key: row for row in rows}
    facts = [
        str((row.result or {}).get("fact"))
        for row in rows
        if row.status in _SUCCESS_ACTION_STATUSES and (row.result or {}).get("fact")
    ]
    results: dict[str, ActionExecutionResult] = {}
    for row in rows:
        if row.status in _TERMINAL_ACTION_STATUSES:
            res_dict = {"capability": row.capability, **(row.result or {"status": row.status})}
            results[row.action_key] = ActionExecutionResult.model_validate(res_dict)
            continue
        raw_args = row.arguments or {}
        illegal_deps = raw_args.get("illegal_outfit_deps") if isinstance(raw_args, dict) else None
        if isinstance(illegal_deps, list) and illegal_deps and str(row.capability).startswith("scene."):
            result = ActionExecutionResult(
                status="failed",
                reason="场景与衣柜相互独立，不能依赖换装动作表达场景穿着；把完整造型写入 outfit_description",
                dependencies=[str(dep) for dep in illegal_deps],
                capability=row.capability,
            )
            await _set_action_state(row.id, "failed", result.model_dump(exclude_none=True))
            row.status = "failed"
            row.result = result.model_dump(exclude_none=True)
            results[row.action_key] = result
            continue
        dependencies = (row.arguments or {}).get("depends_on", [])
        unsatisfied = [
            dep for dep in dependencies if dep not in by_key or by_key[dep].status not in _SUCCESS_ACTION_STATUSES
        ]
        if unsatisfied:
            result = ActionExecutionResult(
                status="skipped",
                reason="dependency not completed",
                dependencies=unsatisfied,
                capability=row.capability,
            )
            await _set_action_state(row.id, "skipped", result.model_dump(exclude_none=True))
            row.status = "skipped"
            row.result = result.model_dump(exclude_none=True)
            results[row.action_key] = result
            continue
        runtime_allowed, blocked_reason = await _runtime_capability_allowed(
            user_id,
            row.capability,
            row.result if isinstance(row.result, dict) else None,
        )
        if not runtime_allowed:
            result = ActionExecutionResult(
                status="blocked",
                reason=blocked_reason,
                capability=row.capability,
            )
            await _set_action_state(row.id, "blocked", result.model_dump(exclude_none=True))
            row.status = "blocked"
            row.result = result.model_dump(exclude_none=True)
            results[row.action_key] = result
            continue
        executor = _EXECUTORS.get(row.capability)
        if executor is None:
            result = ActionExecutionResult(
                status="blocked",
                reason="capability executor unavailable",
                capability=row.capability,
            )
            await _set_action_state(row.id, "blocked", result.model_dump(exclude_none=True))
        else:
            await _set_action_state(row.id, "running")
            row.status = "running"
            try:
                action_context = context.model_copy(
                    update={
                        "action_row_id": row.id,
                        "resume_result": row.result if isinstance(row.result, dict) else None,
                    },
                )
                result = await executor(
                    user_id,
                    (row.arguments or {}).get("values", {}),
                    action_context,
                    facts,
                    date_context,
                )
            except Exception as exc:
                logger.warning(
                    "nightly capability action failed",
                    extra={
                        "user_id": user_id,
                        "action": row.action_key,
                        "capability": row.capability,
                        "error": str(exc),
                    },
                    exc_info=True,
                )
                result = ActionExecutionResult(status="failed", reason=str(exc))
            status = result.status if result.status in _TERMINAL_ACTION_STATUSES else "failed"
            result.status = status
            result.capability = row.capability
            await _set_action_state(row.id, status, result.model_dump(exclude_none=True))
        row.status = result.status
        row.result = result.model_dump(exclude_none=True)
        if row.status in _SUCCESS_ACTION_STATUSES and result.fact:
            facts.append(result.fact)
        results[row.action_key] = result
    return results


async def _execute_ephemeral_actions(
    plan: NormalizedPlan,
    user_id: int,
    context: PlanningContext,
    date_context: DateContext,
) -> dict[str, ActionExecutionResult]:
    facts: list[str] = []
    results: dict[str, ActionExecutionResult] = {}
    statuses: dict[str, str] = {}
    for action in plan.actions:
        if action.illegal_outfit_deps:
            result = ActionExecutionResult(
                status="failed",
                reason="场景与衣柜相互独立，不能依赖换装动作表达场景穿着；把完整造型写入 outfit_description",
                dependencies=action.illegal_outfit_deps,
                capability=action.capability,
            )
            statuses[action.id] = result.status
            results[action.id] = result
            continue
        unsatisfied = [dep for dep in action.depends_on if statuses.get(dep) not in _SUCCESS_ACTION_STATUSES]
        if unsatisfied:
            result = ActionExecutionResult(
                status="skipped",
                reason="dependency not completed",
                capability=action.capability,
            )
        else:
            runtime_allowed, blocked_reason = await _runtime_capability_allowed(
                user_id,
                action.capability,
            )
            if not runtime_allowed:
                result = ActionExecutionResult(
                    status="blocked",
                    reason=blocked_reason,
                    capability=action.capability,
                )
            else:
                try:
                    result = await _EXECUTORS[action.capability](
                        user_id,
                        action.arguments,
                        context,
                        facts,
                        date_context,
                    )
                    result.capability = action.capability
                except Exception as exc:  # noqa: BLE001 - 临时执行模式同样要求单动作故障隔离
                    result = ActionExecutionResult(
                        status="failed",
                        reason=str(exc),
                        capability=action.capability,
                    )
        statuses[action.id] = result.status
        if result.status in _SUCCESS_ACTION_STATUSES and result.fact:
            facts.append(result.fact)
        results[action.id] = result
    return results


async def run_nightly_planning(
    llm_cfg: dict[str, Any],
    user_id: int,
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    user_profile: dict[str, str],
    recall_highlights: list[dict[str, Any]],
    date_context: DateContext | dict[str, Any],
    anomaly_stats: dict[str, Any],
    today_conversations: list[dict[str, str]],
    *,
    moment_interactions: list[dict[str, Any]] | None = None,
    log_id: int | None = None,
) -> PlanningResult:
    date_ctx = date_context if isinstance(date_context, DateContext) else DateContext.model_validate(date_context)
    context = await _collect_context(user_id)
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
            "autonomous_context": context.model_dump(
                exclude={"action_row_id", "resume_result"},
                exclude_none=True,
            ),
            **date_ctx.model_dump(),
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
        await _persist_plan(
            log_id,
            user_id,
            date.fromisoformat(str(date_ctx.source_date)),
            plan,
        )
    context.plan_theme = plan.theme
    if log_id is not None:
        actions = await _execute_persisted_actions(
            log_id,
            user_id,
            context,
            date_ctx,
        )
    else:
        actions = await _execute_ephemeral_actions(plan, user_id, context, date_ctx)
    return PlanningResult(
        theme=plan.theme,
        rationale=plan.rationale,
        reveal=plan.reveal,
        actions=actions,
    )
