"""夜间自主规划、持久化动作账本与顺序执行。"""

import json
import re
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any, Literal, TypeGuard
from zoneinfo import ZoneInfo

from features.action_library.prompts import ACTION_DESIGN_GUIDANCE
from features.appearance.nightly import list_outfits
from features.appearance.observations import load_visual_observation
from features.companion.planning_queries import PersonaPlanningSnapshot
from features.entries.queries import post_summaries
from features.life.actions import CREATION_CAPABILITIES, LIFE_ACTION, LIFE_CAPABILITIES, NextAction, OutreachAction
from features.life.execution import enqueue_action
from features.life.queries import pursuit_evidence, steps_by_id
from features.life.schemas import LifeStepView
from features.media.identity_rendering import render_character_appearance
from features.memory.contracts import MemoryListItem
from features.scenes.queries import get_scene_state, scene_environment
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.api_ids import ResourceId, parse_resource_id
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language, resolve_prompt_text, utc_now
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from workflows.companion_actions import build_action_view
from workflows.life_capabilities import capability_blocks, capability_schema, load_life_materials
from workflows.life_execution import run_life_execution
from workflows.nightly.models import NightlyActivityAction, NightlyActivityLog
from workflows.nightly.output_contracts import NightlyPlanOutput
from workflows.nightly.prompts import NIGHTLY_JSON_REPAIR_TEXTS, OUTREACH_CONTEXT_TEMPLATES, PLANNING_SYSTEM_PROMPT
from workflows.nightly.window import in_nightly_window, require_nightly_window

logger = get_logger(__name__)

# 计划项只保存未受理结果；已受理项的进展与结果由关联生活步骤给出。
PlanItemStatus = Literal["pending", "skipped", "blocked", "failed"]
ActionStatus = Literal[
    "pending", "running", "succeeded", "skipped", "blocked", "failed", "interrupted", "result_unknown"
]
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

    narrative: str = Field(min_length=1, max_length=1000)
    name: str = Field(default="主动联系", min_length=1, max_length=100)
    # 规划给出用户本地时刻，时区换算由代码完成。
    local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    prompt: str = Field(min_length=1, max_length=4000)


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    capability: str
    phase: int
    pursuit_id: ResourceId | None = None
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
    life_step_id: ResourceId | None = None
    fact: str | None = None
    reason: str | None = None
    outfit_id: ResourceId | None = None
    scene_id: ResourceId | None = None
    cron_job_id: ResourceId | None = None


class PlanningResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    rationale: str = ""
    actions: dict[str, ActionExecutionResult] = Field(default_factory=dict)

    @property
    def pending(self) -> bool:
        return any(result.status in {"pending", "running"} for result in self.actions.values())


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

    language: str = ""
    persona: PersonaContext
    life_context: dict[str, Any] = Field(default_factory=dict)
    pursuit_evidence: dict[str, Any] = Field(default_factory=dict)
    scene: SceneContext
    wardrobe: list[WardrobeItem] = Field(default_factory=list)
    # 模型只读取当前可播能力，制作目标由代码单独冻结。
    actions: dict[str, Any] = Field(default_factory=dict)
    # 影像制作各选一份 ready 素材，由代码冻结，模型只提供制作要求。
    visual_materials: VisualMaterialSelection | None = Field(default=None, exclude=True)
    recent_autonomous_actions: list[RecentActionSummary] = Field(default_factory=list)
    recent_posts: list[RecentPostSummary] = Field(default_factory=list)
    available_capabilities: list[NightlyCapability] = Field(default_factory=list)
    blocked_capabilities: list[BlockedCapability] = Field(default_factory=list)
    plan_theme: str = ""


def _planning_context_payload(context: PlanningContext) -> dict[str, Any]:
    """模型读取规划资料；已冻结的制作素材只提供标识，执行目标保留在代码上下文。"""

    payload = context.model_dump(exclude_none=True)
    payload["outfits"] = payload.pop("wardrobe", [])
    if context.visual_materials is not None:
        payload["visual_materials"] = context.visual_materials.model_dump()
    return payload


_NIGHTLY_PHASES = {
    "outfit.create": 10,
    "scene.create": 20,
    "action.design": 25,
    "visual.generate": 30,
    "outreach.schedule": 40,
}


def _nightly_capability(name: str) -> NightlyCapability:
    definition = LIFE_CAPABILITIES[name]
    schema = capability_schema(name, model=OutreachScheduleArgs if name == "outreach.schedule" else None)
    if name == "visual.generate":
        for key in ("outfit_id", "scene_id", "action_id"):
            schema["properties"].pop(key)
            schema["required"].remove(key)
    schema["properties"]["narrative"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": 1000,
        "description": "伙伴面向用户的第一人称计划与缘由，不代表已经完成",
    }
    schema["required"] = list(dict.fromkeys([*schema["required"], "narrative"]))
    return NightlyCapability(
        name=name,
        phase=_NIGHTLY_PHASES[name],
        description=definition.description,
        arguments=schema,
        exclusive_group=name.split(".", maxsplit=1)[0],
        paid=name in CREATION_CAPABILITIES,
    )


_CAPABILITY_BY_NAME = {name: _nightly_capability(name) for name in _NIGHTLY_PHASES}


def _is_action_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= 48 and not _ACTION_ID_PATTERN.search(value)


async def _collect_context(
    user_id: int,
    timezone: ZoneInfo,
    *,
    life_context: dict[str, Any],
    persona: PersonaPlanningSnapshot | None,
    language: str,
) -> PlanningContext:
    materials = await load_life_materials(user_id)
    blocked = await capability_blocks(user_id, source="nightly", materials=materials)
    async with SESSION_LOCAL() as db:
        outfits = await list_outfits(db, user_id)
        scene = await get_scene_state(db, user_id)
        observation = await load_visual_observation(db, user_id)
        recent_actions = (
            await db.scalars(
                select(NightlyActivityAction)
                .where(NightlyActivityAction.user_id == user_id)
                .order_by(NightlyActivityAction.id.desc())
                .limit(40)
            )
        ).all()
        recent_posts = await post_summaries(db, user_id, limit=20)
        steps = await steps_by_id(
            db, user_id, [row.life_step_id for row in recent_actions if row.life_step_id is not None]
        )
        evidence = await pursuit_evidence(
            db, user_id, [parse_resource_id(item["id"]) for item in life_context.get("active_pursuits", [])]
        )
        action_view = await build_action_view(user_id)
        action_snapshot = {"ready_actions": [item.model_dump() for item in action_view.items]}
        # 影像制作各选一份 ready 素材（最新可用项），模型只提供制作要求。
        visual_materials = None
        if selected := materials.visual:
            outfit, environment, action = selected
            visual_materials = VisualMaterialSelection(
                outfit_id=outfit.id, scene_id=environment.id, action_id=action.id
            )

    definition = dict(persona.definition) if persona is not None else {}
    if observation is not None:
        definition["visual_observation"] = render_character_appearance(observation, language=language)
    recent_action_summaries = []
    for row in recent_actions:
        result = _action_result(row, steps)
        recent_action_summaries.append(
            RecentActionSummary(
                date=row.target_date.isoformat(), capability=row.capability, status=result.status, fact=result.fact
            )
        )
    context = PlanningContext(
        language=language,
        persona=PersonaContext(complete=bool(persona and persona.is_complete), definition=definition),
        life_context=life_context,
        pursuit_evidence=evidence.prompt_payload(),
        scene=SceneContext(
            environment=scene_environment(scene),
            generation_pending=scene.pending is not None or scene.regenerating is not None,
        ),
        wardrobe=[
            WardrobeItem(id=outfit.id, name=outfit.name, description=outfit.description or "", status=outfit.status)
            for outfit in outfits
        ],
        actions=action_snapshot,
        visual_materials=visual_materials,
        recent_autonomous_actions=recent_action_summaries,
        # 与动作的 target_date 一样按用户本地日标注。
        recent_posts=[
            RecentPostSummary(
                date=occurred_at.astimezone(timezone).date().isoformat(), content_type=content_type, title=title
            )
            for occurred_at, content_type, title in recent_posts
        ],
    )
    for name, capability in _CAPABILITY_BY_NAME.items():
        reason = blocked[name].reason if name in blocked else None
        if name == "scene.create" and context.scene.generation_pending:
            reason = reason or "已有环境图片正在生成"
        if name == "visual.generate" and context.visual_materials is None:
            reason = reason or "缺少可用的造型、环境或动作"
        if reason:
            context.blocked_capabilities.append(BlockedCapability(name=name, reason=reason))
        else:
            context.available_capabilities.append(capability)
    return context


def _normalize_plan(parsed: Any, context: PlanningContext) -> NormalizedPlan:
    raw_plan = NightlyPlanOutput.model_validate(parsed)
    seen_ids: set[str] = set()
    for action in raw_plan.actions:
        if action.id in seen_ids:
            raise LlmJsonValidationError("duplicate_action_id", fields=("id",))
        seen_ids.add(action.id)
    available_names = {item.name for item in context.available_capabilities}
    active_pursuits = {parse_resource_id(item["id"]) for item in context.life_context.get("active_pursuits", [])}
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
        if action.pursuit_id is not None and action.pursuit_id not in active_pursuits:
            raise LlmJsonValidationError("unknown_pursuit", fields=("pursuit_id",))
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
                pursuit_id=action.pursuit_id,
                depends_on=dependencies,
                arguments={
                    **action.arguments,
                    **(
                        context.visual_materials.model_dump()
                        if capability_name == "visual.generate" and context.visual_materials is not None
                        else {}
                    ),
                },
            )
        )
    outfit_ids = {action.id for action in actions if action.capability.startswith("outfit.")}
    for action in actions:
        if action.capability.startswith("scene."):
            action.illegal_outfit_deps = [dep for dep in action.depends_on if dep in outfit_ids]
    # 稳定排序：同阶段保持规划顺序。
    actions.sort(key=lambda action: action.phase)
    return NormalizedPlan(theme=raw_plan.theme.strip(), rationale=raw_plan.rationale.strip(), actions=actions)


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
        log.payload = {**(log.payload or {}), "nightly_plan": plan.model_dump(exclude={"actions"})}
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
                    "pursuit_id": action.pursuit_id,
                    **({"illegal_outfit_deps": action.illegal_outfit_deps} if action.illegal_outfit_deps else {}),
                },
            )
            for action in plan.actions
        )
        await db.commit()


async def _load_action_rows(log_id: int) -> list[NightlyActivityAction]:
    """读取动作账本；执行幂等由生活步骤的 nightly 请求身份保证，中断后直接续跑。"""
    async with SESSION_LOCAL() as db:
        return list(
            (
                await db.scalars(
                    select(NightlyActivityAction)
                    .where(NightlyActivityAction.log_id == log_id)
                    .order_by(NightlyActivityAction.phase, NightlyActivityAction.id)
                )
            ).all()
        )


def _action_result(row: NightlyActivityAction, steps: dict[int, LifeStepView]) -> ActionExecutionResult:
    if row.life_step_id is None:
        return ActionExecutionResult(status=row.status, capability=row.capability, reason=row.reason)
    step = steps.get(row.life_step_id)
    if step is None:
        return ActionExecutionResult(
            status="interrupted", capability=row.capability, life_step_id=row.life_step_id, reason="关联步骤已不存在"
        )
    status: ActionStatus = {
        "planned": "pending",
        "running": "running",
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "blocked",
        "unknown": "result_unknown",
    }[step.state]
    # 事实只陈述步骤已经确认的结果，不把能力说明或计划写成经历。
    facts = {
        "outfit.create": "已完成并保存造型素材",
        "scene.create": "已完成并保存环境图片",
        "action.design": "动作定义已通过评审",
        "visual.generate": "已完成并保存图片或视频",
    }
    return ActionExecutionResult(
        status=status,
        capability=row.capability,
        life_step_id=step.id,
        fact=facts.get(row.capability) if step.state == "completed" else None,
        reason=step.error or step.wait_reason or None,
        outfit_id=step.result_ref.get("outfit_id"),
        scene_id=step.result_ref.get("scene_id"),
        cron_job_id=step.result_ref.get("cron_job_id"),
    )


async def load_action_results(log_id: int) -> dict[str, ActionExecutionResult]:
    return await _read_action_results(await _load_action_rows(log_id))


async def _read_action_results(rows: list[NightlyActivityAction]) -> dict[str, ActionExecutionResult]:
    if not rows:
        return {}
    async with SESSION_LOCAL() as db:
        steps = await steps_by_id(
            db, rows[0].user_id, [row.life_step_id for row in rows if row.life_step_id is not None]
        )
    return {row.action_key: _action_result(row, steps) for row in rows}


def _action_input(
    row: NightlyActivityAction, *, language: str, plan_theme: str, date_context: DateContext, facts: list[str]
) -> NextAction:
    arguments = (row.arguments or {}).get("values", {})
    payload = {key: value for key, value in arguments.items() if key != "narrative"}
    payload["capability"] = row.capability
    if row.capability == "outreach.schedule":
        parsed = OutreachScheduleArgs.model_validate(arguments)
        timezone = ZoneInfo(date_context.user_timezone)
        target = date.fromisoformat(date_context.tomorrow_date)
        hour, minute = map(int, parsed.local_time.split(":"))
        prompt = OUTREACH_CONTEXT_TEMPLATES[resolve_language(language)].format(
            prompt=parsed.prompt,
            context=json.dumps(
                {"completed_nightly_action_facts": facts, "nightly_theme": plan_theme}, ensure_ascii=False
            ),
        )
        payload = OutreachAction(
            capability="outreach.schedule",
            name=parsed.name,
            prompt=prompt,
            scheduled_at=datetime.combine(target, time(hour, minute), timezone),
            expires_at=datetime.combine(target + timedelta(days=1), time.min, timezone),
        ).model_dump()
    return NextAction(
        description=str(
            arguments.get("requirements")
            or arguments.get("description")
            or arguments.get("notes")
            or arguments.get("reason")
            or row.capability
        )[:1000],
        narrative=arguments.get("narrative"),
        action=LIFE_ACTION.validate_python(payload),
    )


async def _record_plan_outcome(row: NightlyActivityAction, status: PlanItemStatus, reason: str) -> None:
    async with SESSION_LOCAL() as db:
        current = await db.get(NightlyActivityAction, row.id)
        if current is not None and current.life_step_id is None:
            current.status, current.reason = status, reason
            await db.commit()
            row.status, row.reason = status, reason


async def _link_action_step(row: NightlyActivityAction, user_id: int, action: NextAction) -> int:
    """计划项与生活步骤在同一事务关联；已有步骤直接复用。"""
    async with SESSION_LOCAL() as db:
        current = await db.get(NightlyActivityAction, row.id, with_for_update=True)
        if current is None:
            raise ValueError("夜间计划已不存在")
        if current.life_step_id is None:
            planned_pursuit = (row.arguments or {}).get("pursuit_id")
            step = await enqueue_action(
                db,
                user_id,
                action,
                request_id=f"nightly:{row.id}",
                pursuit_id=parse_resource_id(planned_pursuit)
                if planned_pursuit is not None and row.capability != "outreach.schedule"
                else None,
                source="nightly",
            )
            current.life_step_id = step.id
            await db.commit()
        row.life_step_id = current.life_step_id
        return current.life_step_id


def _blocked_plan_outcome(
    arguments: dict[str, Any], dependencies: list[ActionExecutionResult | None], date_context: DateContext
) -> tuple[PlanItemStatus, str] | None:
    if arguments.get("illegal_outfit_deps"):
        return "failed", "环境与造型相互独立，环境动作不能依赖造型动作"
    if any(result is None or result.status != "succeeded" for result in dependencies):
        return "skipped", "dependency not completed"
    if not in_nightly_window(utc_now(), ZoneInfo(date_context.user_timezone)):
        return "blocked", "nightly window ended"
    return None


async def _execute_persisted_actions(
    log_id: int, user_id: int, *, language: str, plan_theme: str, date_context: DateContext
) -> dict[str, ActionExecutionResult]:
    rows = await _load_action_rows(log_id)
    results = await _read_action_results(rows)
    for row in rows:
        if row.life_step_id is None:
            if row.status != "pending":
                continue
            arguments = row.arguments or {}
            dependencies = [results.get(key) for key in arguments.get("depends_on", [])]
            if any(result is not None and result.status in {"pending", "running"} for result in dependencies):
                continue
            if outcome := _blocked_plan_outcome(arguments, dependencies, date_context):
                await _record_plan_outcome(row, *outcome)
                results[row.action_key] = _action_result(row, {})
                continue
            try:
                facts = [result.fact for result in results.values() if result.status == "succeeded" and result.fact]
                action = _action_input(
                    row, language=language, plan_theme=plan_theme, date_context=date_context, facts=facts
                )
                row.life_step_id = await _link_action_step(row, user_id, action)
            except Exception as exc:
                logger.exception(
                    "nightly action admission failed", extra={"user_id": user_id, "action": row.action_key}
                )
                if row.life_step_id is None:
                    await _record_plan_outcome(row, "failed", str(exc))
                    results[row.action_key] = _action_result(row, {})
                    continue
        try:
            await run_life_execution(user_id, step_id=row.life_step_id)
        except Exception:
            logger.exception("nightly step recovery failed", extra={"user_id": user_id, "step_id": row.life_step_id})
        # 查询失败向上报告，不因此再次推进已经受理的步骤。
        results.update(await _read_action_results([row]))
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
    persona: PersonaPlanningSnapshot | None,
    life_context: dict[str, Any],
    language: str,
    topic_reserve: list[dict[str, Any]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> PlanningResult:
    timezone = ZoneInfo(date_context.user_timezone)
    plan = await _stored_plan(log_id)
    if plan is None:
        if not in_nightly_window(utc_now(), timezone):
            return PlanningResult(rationale="夜间窗口已结束")
        context = await _collect_context(
            user_id, timezone, life_context=life_context, persona=persona, language=language
        )
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
        instructions = PLANNING_SYSTEM_PROMPT
        if any(cap.name == "action.design" for cap in context.available_capabilities):
            instructions += "\n\naction.design: " + resolve_prompt_text(ACTION_DESIGN_GUIDANCE, "en")
        plan = await call_llm_json(
            await load_user_llm_config(user_id, AiTaskKey.nightly),
            instructions,
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
    actions = await _execute_persisted_actions(
        log_id, user_id, language=language, plan_theme=plan.theme, date_context=date_context
    )
    return PlanningResult(theme=plan.theme, rationale=plan.rationale, actions=actions)
