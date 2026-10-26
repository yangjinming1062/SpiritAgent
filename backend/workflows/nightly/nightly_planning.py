"""夜间自主规划、持久化动作账本与顺序执行。"""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any, Literal, TypeGuard
from zoneinfo import ZoneInfo

from features.accounts.queries import nightly_account
from features.action_library.queries import list_actions
from features.appearance.character_card import load_character_snapshot
from features.appearance.nightly import list_outfits
from features.automation.cron_jobs import create_job, remove_job
from features.companion.planning_queries import PersonaPlanningSnapshot, planning_persona
from features.configuration.settings.values import load_user_settings
from features.entries.queries import post_summaries
from features.life.actions import LIFE_ACTION, NextAction
from features.life.context import read_life_context_in_session
from features.life.execution import claim_step, finish_step, mark_step_submitted, reserve_external_action
from features.life.use_cases import read_step, step_for_request
from features.media.identity_rendering import render_character_appearance
from features.media.image_generation import resolve_image_gen_chain
from features.memory.contracts import MemoryListItem
from features.scenes.queries import get_scene_state, scene_environment
from features.visuals.queries import presentation_state
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId, parse_resource_id
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.companion_action_prompts import ACTION_DESIGN_MOTION_DESCRIPTION
from workflows.companion_actions import build_action_view
from workflows.life_execution import submit_life_action
from workflows.nightly.models import NightlyActivityAction, NightlyActivityLog
from workflows.nightly.output_contracts import NightlyPlanOutput
from workflows.nightly.prompts import (
    NIGHTLY_JSON_REPAIR_TEXTS,
    OUTREACH_CONTEXT_TEMPLATES,
    PLANNING_SYSTEM_PROMPT,
)
from workflows.nightly.window import in_nightly_window, require_nightly_window

logger = get_logger(__name__)

_POLL_SECONDS = 3.0
ActionStatus = Literal["succeeded", "skipped", "blocked", "failed", "interrupted", "result_unknown"]
_TERMINAL_ACTION_STATUSES = frozenset(
    ("succeeded", "skipped", "blocked", "failed", "interrupted", "result_unknown"),
)
_ACTION_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


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


class OutreachScheduleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="主动联系", max_length=100)
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
    # 规划产生的非法环境素材→造型素材依赖，执行时产生可诊断失败。
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
    outfit_id: ResourceId | None = None
    scene_id: ResourceId | None = None
    cron_job_id: ResourceId | None = None
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
    image_text: bool = False
    video: bool = False


class PersonaContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    complete: bool = False
    definition: dict[str, Any] = Field(default_factory=dict)


class SceneContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    environment: dict[str, Any] = Field(default_factory=dict)
    generation_pending: bool = False


class WardrobeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: ResourceId
    name: str
    description: str = ""
    status: str


class VisualMaterialSelection(BaseModel):
    """规划期选定的一组 ready 素材；影像制作按这组冻结组合，不重新挑选。"""

    model_config = ConfigDict(extra="ignore")

    outfit_id: ResourceId
    scene_id: ResourceId
    action_id: ResourceId


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
    life_context: dict[str, Any] = Field(default_factory=dict)
    scene: SceneContext
    wardrobe: list[WardrobeItem] = Field(default_factory=list)
    # 模型只读取当前可播能力，制作目标由代码单独冻结。
    actions: dict[str, Any] = Field(default_factory=dict)
    action_design_enabled: bool = Field(default=False, exclude=True)
    # 影像制作各选一份 ready 素材，由代码冻结，模型只提供制作要求。
    visual_materials: VisualMaterialSelection | None = Field(default=None, exclude=True)
    recent_autonomous_actions: list[RecentActionSummary] = Field(default_factory=list)
    recent_posts: list[RecentPostSummary] = Field(default_factory=list)
    available_capabilities: list[NightlyCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    plan_theme: str = ""


@dataclass(frozen=True)
class _ActionRun:
    """单个账本动作的执行上下文。"""

    user_id: int
    row_id: int
    context: PlanningContext
    date_context: DateContext
    facts: list[str]


def _planning_context_payload(context: PlanningContext) -> dict[str, Any]:
    """模型读取规划资料；已冻结的制作素材只提供标识，执行目标保留在代码上下文。"""

    payload = context.model_dump(exclude_none=True)
    payload["outfits"] = payload.pop("wardrobe", [])
    if context.visual_materials is not None:
        payload["visual_materials"] = context.visual_materials.model_dump()
    return payload


_CAPABILITIES: tuple[NightlyCapability, ...] = (
    NightlyCapability(
        name="outfit.create",
        phase=10,
        description="构思并生成一套新的穿着造型并保存；只在已有造型都不合适或有特殊节点时使用。",
        arguments={
            "description": "string（非空，最多 4000 字符）：完整的新造型设计，包括服装、配色及需要改变的发型、妆容或配饰；保持角色固定身份，不写场景或动作。",
        },
        exclusive_group="outfit",
        paid=True,
    ),
    NightlyCapability(
        name="scene.create",
        phase=20,
        description="已有环境图片不合适时新建一张。notes 描述地点、陈设、光线与氛围，必须非空；统一按真实拍摄的环境照片呈现，不写画风。"
        "不描绘你本人，其他人物、动物、肖像或雕像可以按环境需要出现；与造型相互独立，也不改动正在呈现的画面。",
        arguments={
            "notes": "string（非空）：环境的地点、陈设、光线与氛围，统一按真实拍摄的环境照片呈现，不写画风；不描绘你本人，其他人物、动物、肖像或雕像可按环境需要安排。",
        },
        exclusive_group="scene",
        paid=True,
    ),
    NightlyCapability(
        name="outreach.schedule",
        phase=40,
        description="为次日保留有具体缘由的联系意图，触发时结合最新情境决定是否开口及如何表达；"
        "从计划时间起等待用户在线，最晚保留到用户本地次日结束。",
        arguments={
            "name": "string（可选，最多 100 字符）：主动联系任务的简短名称，省略时为主动联系。",
            "local_time": "string（非空，HH:MM，24 小时制）：用户本地时间，在 tomorrow_date 当天这一时刻开始等待用户在线；时区换算由系统完成。",
            "prompt": "string（非空，最多 4000 字符）：面向届时主动回合的自包含任务说明，交代有依据的背景、联系目的、"
            "需要交流或核实的事项，以及必要的时效和不联系条件，有时效的内容用给定日期标明；可说明语气，不写问候正文或示例台词，"
            "不预设届时用户状态或把未完成活动写成事实。",
        },
        exclusive_group="outreach",
    ),
    NightlyCapability(
        name="action.design",
        phase=25,
        description="提出一个有明确用途、可反复使用的新动作，先查看 actions.ready_actions 是否已有合适表达。"
        "确有缺口才使用，没有具体价值时可以不设计；同一动作由系统统一去重和评审。"
        "自主创意不代表用户提出过同样要求。登记、制作完成与实际表演分别确认，后续内容不能将登记写成已做好或已表演。",
        arguments={
            "name": "string（1–64 字符）：动作的简短显示名称，不是动作标识或运动脚本。",
            "motion_description": "string（10–600 字符）：" + ACTION_DESIGN_MOTION_DESCRIPTION,
            "use_when": "list[string]（可选，最多 8 项，每项最多 120 字符）：适合使用该动作的交流情境，省略或空列表表示没有补充适用条件。",
            "avoid_when": "list[string]（可选，最多 8 项，每项最多 120 字符）：应避免使用该动作的情境，省略或空列表表示没有补充避免条件。",
            "reason": "string（1–400 字符）：现有动作无法满足的表达需要，以及本次新增动作的具体价值。",
            "duration_seconds": "integer（可选，默认4，1–15）：单个动作片段的时长，单位为秒；按可见过程与必要停留选择，长期陪伴不等于更长的单片段。完成后的时间可安定停留。",
            "clip_kind": "string（可选，默认once）：loop 表示姿态、接触及物品可见状态在接点自然连续的持续状态或运动，不为循环另编往返或重置；不能自然闭合时选 once 或仅设计可持续阶段。once 是有自然收束的完整动作，制作后也可重复使用。",
        },
        paid=True,
    ),
    NightlyCapability(
        name="visual.create",
        phase=30,
        description="按具体用途制作一份图片或视频：requirements 写清画面内容与用途，media_kind 选 image 或 video。"
        "组合固定使用 visual_materials 指定的造型、环境与动作，不改动它们本身；requirements 不要另选或假定其他造型、环境或动作。"
        "受理不等于完成，成品保存后才能实际呈现。",
        arguments={
            "requirements": "string（非空）：画面内容、构图与用途要求，按真实拍摄呈现，不写画风；须与 visual_materials 中的造型、环境与动作相容。",
            "media_kind": "string（image 或 video）：成品类型，需要可见运动过程时选 video。",
        },
        exclusive_group="visual",
        paid=True,
    ),
)
_CAPABILITY_BY_NAME = {cap.name: cap for cap in _CAPABILITIES}


def _invalid_arguments(exc: ValidationError) -> ActionExecutionResult:
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors(include_url=False)
    )
    return ActionExecutionResult(status="failed", reason=f"invalid arguments: {details}")


def _is_action_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= 48 and not _ACTION_ID_PATTERN.search(value)


def _policies(persona: PersonaPlanningSnapshot | None) -> PlanningPolicies:
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
    *,
    task: AiTaskKey,
) -> bool:
    try:
        return bool(await resolve_provider_chain(db, user_id, service_type, task=task))
    except Exception:
        logger.warning(
            "nightly capability availability check failed",
            extra={"user_id": user_id, "service_type": service_type},
            exc_info=True,
        )
        return False


async def _image_provider_available(
    db: AsyncSession,
    user_id: int,
    *,
    task: AiTaskKey,
    has_reference: bool,
) -> bool:
    try:
        chain = await resolve_image_gen_chain(db, user_id, task=task, has_reference=has_reference)
        return bool(chain.providers)
    except Exception:
        logger.warning(
            "nightly image capability check failed",
            extra={"user_id": user_id, "has_reference": has_reference},
            exc_info=True,
        )
        return False


def _capability_availability(
    context: PlanningContext,
) -> tuple[list[NightlyCapability], list[BlockedCapability]]:
    providers = context.providers
    persona_ready = bool(context.persona.complete)
    # 政策拦截由 _policy_block 统一判断；这里只列素材与供应商条件，原因文案覆盖两类拦截。
    rules: dict[str, tuple[bool, str]] = {
        "outfit.create": (providers.image_reference and persona_ready, "造型已锁定或形象、图片生成不可用"),
        "scene.create": (
            providers.image_text and persona_ready and not context.scene.generation_pending,
            "环境已锁定、人设或图片生成不可用，或已有环境图片正在生成",
        ),
        "outreach.schedule": (True, ""),
        "action.design": (
            context.action_design_enabled,
            "自主登记已关闭，或当前形象缺少可用的冻结参考",
        ),
        "visual.create": (
            context.visual_materials is not None and (providers.image_reference or providers.video),
            "缺少可用的造型、环境或动作，或图片、视频生成不可用",
        ),
    }
    available: list[NightlyCapability] = []
    blocked: list[BlockedCapability] = []
    for capability in _CAPABILITIES:
        ready, reason = rules[capability.name]
        if ready and _policy_block(capability.name, context.policies) is None:
            if capability.name == "visual.create" and not providers.video:
                capability = capability.model_copy(
                    update={
                        "description": f"{capability.description}当前没有可用的视频生成，media_kind 只能选 image。",
                    },
                )
            available.append(capability)
        else:
            blocked.append(BlockedCapability(name=capability.name, reason=reason))
    return available, blocked


async def _collect_context(user_id: int, timezone: ZoneInfo) -> PlanningContext:
    async with SESSION_LOCAL() as db:
        persona = await planning_persona(db, user_id)
        life_context = await read_life_context_in_session(db, user_id)
        outfits = await list_outfits(db, user_id)
        scene = await get_scene_state(db, user_id)
        character = await load_character_snapshot(db, user_id)
        settings = await load_user_settings(db, user_id, ("language",))
        reference_image_available = await _image_provider_available(
            db,
            user_id,
            task=AiTaskKey.outfit,
            has_reference=True,
        )
        text_image_available = await _image_provider_available(
            db,
            user_id,
            task=AiTaskKey.scene,
            has_reference=False,
        )
        video_available = await _provider_available(db, user_id, "video_gen", task=AiTaskKey.visuals)
        recent_actions = (
            await db.scalars(
                select(NightlyActivityAction)
                .where(NightlyActivityAction.user_id == user_id)
                .order_by(NightlyActivityAction.id.desc())
                .limit(40),
            )
        ).all()
        recent_posts = await post_summaries(db, user_id, limit=20)
        action_view = await build_action_view(user_id)
        action_snapshot = {"ready_actions": [item.model_dump() for item in action_view.items]}
        presentation_state_row = await presentation_state(user_id)
        action_design_enabled = bool(
            SETTINGS.action_autocreate_enabled
            and presentation_state_row is not None
            and presentation_state_row.autonomous_enabled,
        )
        action_materials = (await list_actions(db, user_id, status="approved", limit=20)).actions
        # 影像制作各选一份 ready 素材（最新可用项），模型只提供制作要求。
        ready_outfit = next((item for item in outfits if item.status == "ready"), None)
        ready_scene = scene.ready[0] if scene.ready else None
        ready_action = action_materials[0] if action_materials else None
        visual_materials = (
            VisualMaterialSelection(
                outfit_id=ready_outfit.id,
                scene_id=ready_scene.id,
                action_id=ready_action.id,
            )
            if ready_outfit is not None and ready_scene is not None and ready_action is not None
            else None
        )

    language = resolve_language(settings.get("language"))
    definition = dict(persona.definition) if persona is not None else {}
    if character is not None:
        definition["fixed_features"] = render_character_appearance(character, language=language)
    context = PlanningContext(
        policies=_policies(persona),
        providers=PlanningProviders(
            image_reference=reference_image_available,
            image_text=text_image_available,
            video=video_available,
        ),
        language=language,
        persona=PersonaContext(
            complete=bool(persona and persona.is_complete),
            definition=definition,
        ),
        life_context=life_context.prompt_payload(),
        scene=SceneContext(
            environment=scene_environment(scene),
            generation_pending=scene.pending is not None or scene.regenerating is not None,
        ),
        wardrobe=[
            WardrobeItem(
                id=outfit.id,
                name=outfit.name,
                description=outfit.description or "",
                status=outfit.status,
            )
            for outfit in outfits
        ],
        actions=action_snapshot,
        action_design_enabled=action_design_enabled,
        visual_materials=visual_materials,
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
        recent_posts=[
            RecentPostSummary(
                date=occurred_at.astimezone(timezone).date().isoformat(),
                content_type=content_type,
                title=title,
            )
            for occurred_at, content_type, title in recent_posts
        ],
    )
    context.available_capabilities, context.blocked_capabilities = _capability_availability(context)
    return context


def _normalize_plan(parsed: Any, context: PlanningContext) -> NormalizedPlan:
    raw_plan = NightlyPlanOutput.model_validate(parsed)
    seen_ids: set[str] = set()
    for action in raw_plan.actions:
        if action.id in seen_ids:
            raise LlmJsonValidationError("duplicate_action_id", fields=("id",))
        seen_ids.add(action.id)
    available_names = {item.name for item in context.available_capabilities}
    seen_groups: set[str] = set()
    paid_count = 0
    actions: list[PlannedAction] = []
    for action in raw_plan.actions:
        if len(actions) >= SETTINGS.life_actions_per_day:
            break
        capability_name = action.capability.strip()
        spec = _CAPABILITY_BY_NAME.get(capability_name)
        if spec is None or capability_name not in available_names:
            continue
        if spec.exclusive_group and spec.exclusive_group in seen_groups:
            continue
        if spec.paid and paid_count >= SETTINGS.life_creations_per_day:
            continue
        # 能力或预算过滤不删除前置条件；执行端对未完成的依赖跳过后续动作。
        # 依赖 ID 无效只跳过该动作，依赖它的后续动作在执行端因前置未完成而跳过。
        dependencies = action.depends_on
        if not all(_is_action_id(dep) for dep in dependencies):
            continue
        if spec.paid:
            paid_count += 1
        if spec.exclusive_group:
            seen_groups.add(spec.exclusive_group)
        actions.append(
            PlannedAction(
                id=action.id,
                capability=capability_name,
                phase=spec.phase,
                depends_on=dependencies,
                arguments=action.arguments,
            ),
        )
    outfit_ids = {action.id for action in actions if action.capability.startswith("outfit.")}
    for action in actions:
        if action.capability.startswith("scene."):
            action.illegal_outfit_deps = [dep for dep in action.depends_on if dep in outfit_ids]
    # 稳定排序：同阶段保持规划顺序。
    actions.sort(key=lambda action: action.phase)
    return NormalizedPlan(
        theme=raw_plan.theme.strip(),
        rationale=raw_plan.rationale.strip(),
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
            # 每项副作用先以 nightly 请求身份登记生活步骤；恢复由原步骤判断是否已提交。
            row.status = "pending"
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
            if result.get("job_id") is not None:
                row.job_id = int(result["job_id"])
        log = await db.get(NightlyActivityLog, row.log_id)
        if log is not None:
            log.updated_at = utc_now()
        await db.commit()


async def _runtime_block_reason(user_id: int, capability: str) -> str | None:
    """动作真正执行前重读总控、政策与开关，避免长耗时计划期间用户关锁后仍产生副作用。"""
    async with SESSION_LOCAL() as db:
        user = await nightly_account(db, user_id)
        persona = await planning_persona(db, user_id)
    if user is None or not user.nightly_activity_enabled:
        return "nightly activity disabled"
    if reason := _policy_block(capability, _policies(persona)):
        return reason
    return None


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
            name=parsed_args.name or "主动联系",
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


async def _execute_life_action(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    request_id = f"nightly:{run.row_id}"
    async with SESSION_LOCAL() as db:
        original = await step_for_request(db, run.user_id, request_id)
        action_row = await db.get(NightlyActivityAction, run.row_id)
        if action_row is None:
            return ActionExecutionResult(status="failed", reason="nightly action no longer exists")
        capability = action_row.capability
    if original is not None:
        if original.action is None:
            return ActionExecutionResult(status="failed", reason="saved action has no executable input")
        action = original.action
    else:
        payload = {"capability": "visual.generate" if capability == "visual.create" else capability, **args}
        if capability == "visual.create":
            materials = run.context.visual_materials
            if materials is None:
                return ActionExecutionResult(status="blocked", reason="no ready materials")
            payload.update(outfit_id=materials.outfit_id, scene_id=materials.scene_id, action_id=materials.action_id)
        action = LIFE_ACTION.validate_python(payload)
    planned_pursuit = run.context.life_context.get("state", {}).get("active_pursuit_id")
    pursuit_id = (
        original.pursuit_id if original else parse_resource_id(planned_pursuit) if planned_pursuit is not None else None
    )
    step = await submit_life_action(
        run.user_id,
        NextAction(
            description=str(
                args.get("requirements")
                or args.get("description")
                or args.get("notes")
                or args.get("reason")
                or capability
            ),
            action=action,
        ),
        request_id=request_id,
        pursuit_id=pursuit_id,
        source="nightly",
    )
    while step.state == "running":
        await asyncio.sleep(_POLL_SECONDS)
        current = await read_step(run.user_id, step.id)
        if current is None:
            return ActionExecutionResult(status="interrupted", reason="execution no longer exists")
        step = current
    if step.state == "planned":
        await finish_step(
            run.user_id, step.id, state="cancelled", error=step.wait_reason or "当前条件不允许开始夜间活动"
        )
        return ActionExecutionResult(status="blocked", reason=step.wait_reason)
    if step.state != "completed":
        return ActionExecutionResult(
            status="result_unknown" if step.state == "unknown" else "failed", reason=step.error or step.wait_reason
        )
    facts = {
        "outfit.create": "已完成并保存造型素材",
        "scene.create": "已完成并保存环境图片",
        "action.design": "动作定义已通过评审",
        "visual.create": "已完成并保存图片或视频",
    }
    return ActionExecutionResult(
        status="succeeded",
        fact=facts[capability],
        outfit_id=step.result_ref.get("outfit_id"),
        scene_id=step.result_ref.get("scene_id"),
    )


async def _execute_budgeted_outreach(run: _ActionRun, args: dict[str, Any]) -> ActionExecutionResult:
    step = await reserve_external_action(
        run.user_id, request_id=f"nightly:{run.row_id}", kind="outreach.schedule", description="安排后续联系"
    )
    if step.state == "completed":
        return ActionExecutionResult(status="succeeded", cron_job_id=step.result_ref.get("cron_job_id"))
    if step.state != "planned":
        return ActionExecutionResult(status="result_unknown", reason="原联系安排须先核对")
    claimed = await claim_step(run.user_id, step.id, ZoneInfo(run.date_context.user_timezone))
    if claimed is None:
        await finish_step(run.user_id, step.id, state="cancelled", error="今日额度或执行条件不满足")
        return ActionExecutionResult(status="blocked", reason="今日额度或执行条件不满足")
    try:
        await mark_step_submitted(run.user_id, step.id)
        result = await _execute_outreach_schedule(run, args)
        await finish_step(
            run.user_id,
            step.id,
            state="completed" if result.status == "succeeded" else "failed",
            result={"cron_job_id": result.cron_job_id} if result.cron_job_id else {},
            error=result.reason,
        )
        return result
    except BaseException:
        await finish_step(run.user_id, step.id, state="unknown", error="联系安排中断，须核对原任务")
        raise


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
            reason="环境与造型相互独立，环境动作不能依赖造型动作",
            dependencies=illegal_deps,
        )
    # 依赖要求前置动作整项成功；部分成功不解锁。
    unsatisfied = [
        dep for dep in arguments.get("depends_on", []) if dep not in by_key or by_key[dep].status != "succeeded"
    ]
    if unsatisfied:
        return ActionExecutionResult(status="skipped", reason="dependency not completed", dependencies=unsatisfied)
    run = _ActionRun(
        user_id=user_id,
        row_id=row.id,
        context=context,
        date_context=date_context,
        facts=facts,
    )
    # 执行前复核与状态写入同样逐项隔离：一项失败只记入该项，不中断后续动作。
    try:
        async with SESSION_LOCAL() as db:
            original = await step_for_request(db, user_id, f"nightly:{row.id}")
        if original is None or original.state == "planned":
            if blocked_reason := await _runtime_block_reason(user_id, row.capability):
                return ActionExecutionResult(status="blocked", reason=blocked_reason)
            if not in_nightly_window(utc_now(), ZoneInfo(date_context.user_timezone)):
                return ActionExecutionResult(status="blocked", reason="nightly window ended")
        await _set_action_state(row.id, "running")
        if row.capability == "outreach.schedule":
            return await _execute_budgeted_outreach(run, arguments.get("values", {}))
        return await _execute_life_action(run, arguments.get("values", {}))
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
        str(row.result["fact"]) for row in rows if row.status == "succeeded" and row.result and row.result.get("fact")
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
            if result.status == "succeeded" and result.fact:
                facts.append(result.fact)
        results[row.action_key] = result
    return results


async def run_nightly_planning(
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
    topic_reserve: list[dict[str, Any]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> PlanningResult:
    timezone = ZoneInfo(date_context.user_timezone)
    context = await _collect_context(user_id, timezone)
    plan = await _stored_plan(log_id)
    if plan is None:
        if not in_nightly_window(utc_now(), timezone):
            return PlanningResult(rationale="夜间窗口已结束")
        payload = {
            "plan_limits": {
                "max_actions": SETTINGS.life_actions_per_day,
                "max_paid_actions": SETTINGS.life_creations_per_day,
            },
            "contextual_memories": contextual_memories,
            "background_memories_state": background_memories,
            "user_profile": user_profile,
            "recall_highlights": [item.model_dump() for item in recall_highlights],
            "today_conversations": today_conversations,
            "topic_reserve": topic_reserve or [],
            **({"post_interactions": post_interactions} if post_interactions else {}),
            "autonomous_context": _planning_context_payload(context),
            "current_time": utc_now().astimezone(timezone).isoformat(),
            **date_context.model_dump(),
            **anomaly_stats,
        }
        plan = await call_llm_json(
            await load_user_llm_config(user_id, AiTaskKey.nightly),
            PLANNING_SYSTEM_PROMPT,
            payload,
            schema=NightlyPlanOutput.model_json_schema(),
            schema_name="nightly_plan",
            parse_output=partial(_normalize_plan, context=context),
            repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, "en"),
            reasoning_effort=NIGHTLY_REASONING_EFFORT,
            before_call=partial(require_nightly_window, timezone),
            diagnostics=diagnostics,
        )
        await _persist_plan(log_id, plan)
    context.plan_theme = plan.theme
    actions = await _execute_persisted_actions(log_id, user_id, context, date_context)
    return PlanningResult(
        theme=plan.theme,
        rationale=plan.rationale,
        actions=actions,
    )
