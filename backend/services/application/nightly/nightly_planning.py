"""夜间自主规划、持久化动作账本与顺序执行。"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, Literal, TypeGuard
from zoneinfo import ZoneInfo

from components import (
    NIGHTLY_PLANNING_REASONING_EFFORT,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    parse_llm_json,
    resolve_language,
    utc_now,
)
from modules.auth import User
from modules.companion import (
    ActionDesignRequest,
    CompanionOutfit,
    CompanionPost,
    CompanionScene,
    Persona,
    SceneOrigin,
    SceneStatus,
)
from modules.scheduler import NightlyActivityAction, NightlyActivityLog
from modules.settings import load_user_settings
from prompts.nightly import NIGHTLY_FACT_TEXTS, OUTREACH_CONTEXT_TEMPLATES, PLANNING_SYSTEM_PROMPT
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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
    confirm_outfit,
    create_outfit_draft,
    resolve_image_gen_chain,
    resume_scene_generation,
    scene_generation_wait_seconds,
    schedule_scene_generation,
)
from services.application.posts import available_types, await_publication, request_publication
from services.contracts import MemoryScope
from services.domains.automation import create_job, remove_job
from services.domains.companion import (
    get_scene_state,
    load_character_snapshot,
    load_persona_definition,
    render_character_appearance,
    scene_environment,
)
from services.domains.memory import MemoryListItem
from services.domains.posts import PostBlockedError, publication_quota_remaining
from services.infrastructure.llm import UserLlmConfig, call_llm_once, resolve_provider_chain

logger = get_logger(__name__)

_SCENE_WAIT_SECONDS = 15 * 60
_POLL_SECONDS = 3.0
ActionStatus = Literal["succeeded", "partial", "skipped", "blocked", "failed", "interrupted", "result_unknown"]
_TERMINAL_ACTION_STATUSES = frozenset(
    ("succeeded", "partial", "skipped", "blocked", "failed", "interrupted", "result_unknown"),
)
_SUCCESS_ACTION_STATUSES = frozenset(("succeeded", "partial"))
# 在途动作记下这些子任务 id 后，进程重启可以核对原任务续跑而不重复付费。
_PROGRESS_KEYS = {"outfit.create": "outfit_id", "scene.create": "scene_id", "post.publish": "publication_id"}
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


class PostPublishArgs(_ActionArgs):
    intent: str = Field(min_length=1, max_length=1000)
    content_type: Literal["auto", "text", "image", "video", "audio"] = "auto"


class OutreachScheduleArgs(_ActionArgs):
    name: str = Field(default="主动问候", max_length=100)
    # 规划给出用户本地时刻，时区换算由代码完成。
    local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    prompt: str = Field(min_length=1, max_length=4000)


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
    dependencies: list[str] | None = None
    outfit_id: int | None = None
    scene_id: int | None = None
    post_id: str | None = None
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


class PlanningProviders(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image_reference: bool = False
    video: bool = False


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


class RecentPostSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    content_type: str
    title: str


class BlockedCapability(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    reason: str


class PlanningContext(BaseModel):
    """规划输入（整体序列化为 autonomous_context）兼执行期共享的只读快照；plan_theme 在计划确定后写入。"""

    model_config = ConfigDict(extra="ignore")

    policies: PlanningPolicies
    providers: PlanningProviders
    language: str = ""
    persona: PersonaContext
    scene: SceneContext
    wardrobe: list[WardrobeItem] = Field(default_factory=list)
    # 动作库摘要：当前包就绪动作、在途提案与近期拒绝，供 action.design 评估缺口。
    actions: dict[str, Any] = Field(default_factory=dict)
    recent_autonomous_actions: list[RecentActionSummary] = Field(default_factory=list)
    recent_posts: list[RecentPostSummary] = Field(default_factory=list)
    available_capabilities: list[NightlyCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    post_types: list[str] = Field(default_factory=list)
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
        name="post.publish",
        phase=30,
        description="表达伙伴想分享的心情、想法或作品，提出社交动态的发布意图，可为文字、图片、视频或语音。"
        "正文与媒体独立创作，发布后在动态页展示和评论。",
        arguments={
            "intent": "string（1–1000字符）：自包含的发布主题、目的及必要内容要求，不写完整动态正文，不把计划或作品描绘的情节写成现实经历。",
            "content_type": "string（可选）：auto或post_types中列出的类型；默认auto，由动态创作时自行选择。",
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
        "后续动态或联系不能以依赖此项为依据宣称动作已做好或已表演。",
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


def _policies(persona: Persona | None) -> PlanningPolicies:
    return PlanningPolicies(
        outfit=persona.outfit_policy if persona is not None else "llm_may_replace",
        scene=persona.scene_policy if persona is not None else "llm_may_replace",
    )


def _policy_block(capability: str, policies: PlanningPolicies) -> str | None:
    """用户政策与开关对能力的拦截原因；规划时的可用性与执行前的复核共用这一规则。"""
    if capability.startswith("outfit.") and policies.outfit == "locked":
        return "outfit policy locked"
    if capability.startswith("scene.") and policies.scene == "locked":
        return "scene policy locked"
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
    *,
    post_quota_available: bool,
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
        "post.publish": (
            bool(context.post_types) and post_quota_available,
            "动态自主发布或创作能力不可用，或最近24小时的自主发布额度已用完",
        ),
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
        settings = await load_user_settings(db, user_id, ("language",))
        reference_image_available = await _reference_image_provider_available(db, user_id)
        video_available = await _provider_available(db, user_id, "video_gen")
        recent_actions = (
            await db.scalars(
                select(NightlyActivityAction)
                .where(NightlyActivityAction.user_id == user_id)
                .order_by(NightlyActivityAction.id.desc())
                .limit(40),
            )
        ).all()
        recent_posts = (
            await db.execute(
                select(CompanionPost.published_at, CompanionPost.content_type, CompanionPost.title)
                .where(CompanionPost.user_id == user_id)
                .order_by(CompanionPost.published_at.desc())
                .limit(20),
            )
        ).all()
        post_quota_available = await publication_quota_remaining(db, user_id, "autonomous") > 0
        # 动作库摘要在会话生命周期内读取，避免 session 关闭后重开未托管事务。
        action_snapshot = await build_action_context(db, user_id)

    language = resolve_language(settings.get("language"))
    definition = load_persona_definition(persona)
    if character is not None:
        definition["fixed_features"] = render_character_appearance(character, language=language)
    context = PlanningContext(
        policies=_policies(persona),
        providers=PlanningProviders(
            image_reference=reference_image_available,
            video=video_available,
        ),
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
        post_types=await available_types(user_id, autonomous=True),
        recent_posts=[
            RecentPostSummary(
                date=occurred_at.astimezone(timezone).date().isoformat(),
                content_type=content_type,
                title=title,
            )
            for occurred_at, content_type, title in recent_posts
        ],
    )
    context.available_capabilities, context.blocked_capabilities = _capability_availability(
        context,
        post_quota_available=post_quota_available,
    )
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
        is_media = capability_name == "post.publish"
        if is_media and media_count >= _MAX_MEDIA_ACTIONS:
            continue
        # 能力或预算过滤不删除前置条件；执行端对未完成的依赖跳过后续动作。
        # null 视为没有依赖；依赖写法无效只跳过该动作，依赖它的后续动作在执行端因前置未完成而跳过。
        dependencies = raw.get("depends_on") or []
        if not isinstance(dependencies, list) or not all(_is_action_id(dep) for dep in dependencies):
            continue
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
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
    if reason := _policy_block(capability, _policies(persona)):
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


async def _execute_post_publish(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    try:
        parsed = PostPublishArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_arguments(exc)
    task_id = run.resume.get("publication_id")
    if task_id is None:
        # 规划只会看到 post_types 中的类型，选列表外的类型属于规划违约；列表为空说明自主发布已关闭，交给受理判定。
        if run.context.post_types and parsed.content_type not in ("auto", *run.context.post_types):
            return ActionExecutionResult(
                status="failed",
                reason="invalid arguments: content_type: not listed in post_types",
            )
        # 规划后开关、类型或额度发生变化时受理被拦截，记为阻止而非失败。
        try:
            result = await request_publication(
                run.user_id,
                key=f"nightly:{run.row_id}",
                trigger="nightly",
                intent=parsed.intent,
                requested_type=parsed.content_type,
                activity_date=date.fromisoformat(run.date_context.source_date),
            )
        except PostBlockedError as exc:
            return ActionExecutionResult(status="blocked", reason=str(exc))
        task_id = result.publication_id
        await _save_progress(run, publication_id=task_id)
    result = await await_publication(run.user_id, str(task_id))
    if result.status == "declined":
        return ActionExecutionResult(status="skipped", reason="伙伴决定本次不发布")
    if result.status == "blocked":
        return ActionExecutionResult(status="blocked", reason=result.error)
    if result.status == "result_unknown":
        return ActionExecutionResult(status="result_unknown", reason=result.error or "原制作结果尚未确认，未重新提交")
    if result.status == "discarded":
        return ActionExecutionResult(status="skipped", reason="本次动态已被明确放弃")
    if result.status not in ("published", "partial"):
        return ActionExecutionResult(status="failed", reason=result.error or "动态尚未发布")
    async with SESSION_LOCAL() as db:
        title = await db.scalar(
            select(CompanionPost.title).where(
                CompanionPost.id == result.post_id,
                CompanionPost.user_id == run.user_id,
            ),
        )
    if title is None:
        return ActionExecutionResult(status="failed", reason="发布任务缺少动态记录")
    return ActionExecutionResult(
        status="partial" if result.status == "partial" else "succeeded",
        post_id=result.post_id,
        fact=_fact(run, "post_published", title=title),
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
    hour, minute = (int(part) for part in parsed_args.local_time.split(":"))
    planned = datetime.combine(target, time(hour, minute), timezone).astimezone(UTC)
    # 恢复执行时原定时刻可能已过，改为尽快开始等待；其余情况按规划时刻。
    schedule = (
        _near_term_cron(now) if planned <= now else f"{planned.minute} {planned.hour} {planned.day} {planned.month} *"
    )
    job = await schedule_on_target_day(schedule)
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
        request = ActionDesignRequest.model_validate({key: value for key, value in args.items() if key in documented})
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
    "post.publish": _execute_post_publish,
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
    recall_highlights: list[MemoryListItem],
    date_context: DateContext,
    anomaly_stats: dict[str, Any],
    today_conversations: list[dict[str, str]],
    post_interactions: list[dict[str, Any]],
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
            "recall_highlights": [item.model_dump() for item in recall_highlights],
            "today_conversations": today_conversations,
            **({"post_interactions": post_interactions} if post_interactions else {}),
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
