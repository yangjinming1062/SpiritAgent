"""按已保存的伙伴决定准备完整影像。"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import timedelta
from itertools import zip_longest
from typing import Literal
from uuid import uuid4

from features.accounts.queries import user_is_active
from features.action_library.queries import ActionPlanningMaterial, approved_action_planning_materials
from features.appearance.identity import load_visual_identity
from features.appearance.queries import initial_outfit_snapshot, ready_outfit_snapshot
from features.appearance.query_contracts import OutfitSnapshot
from features.companion.persona_service import onboarded_user_ids
from features.companion.planning_queries import planning_persona
from features.companion.rhythm import read_rhythm, rhythm_prompt_context
from features.configuration.settings.values import get_user_setting
from features.life.queries import empty_state_view, get_life_state_view, upcoming_shared_visual_context
from features.media.identity_contracts import VisualIdentitySnapshot
from features.media.image_generation import resolve_image_gen_chain
from features.scenes.queries import ScenePlanningMaterial, ready_scene_planning_materials, scene_snapshot
from features.visuals.eligibility import existing_asset
from features.visuals.queries import (
    creation_preferences,
    creation_request,
    read_current_outfit_reference,
    visual_job_detail,
)
from features.visuals.repository import VisualNotFoundError, VisualStateError
from features.visuals.schemas import ActorOpeningFrame, CinematicSource, VisualJobView, VisualSnapshot
from features.visuals.service import create_visual_job
from features.visuals.stage_preparation import (
    InventoryOpening,
    PlannedOpening,
    PreparationChoice,
    PreparationDecision,
    PreparationItem,
    PreparationRequest,
    PreparationStock,
    SavedPreparation,
    active_preparation_job,
    lock_current_preparation,
    preparation_input_revision,
    preparation_result_version,
    preparation_stock,
    recent_preparation_work,
    save_preparation,
    saved_preparation,
)
from features.visuals.stage_selection import project_stage_intent
from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.llm_client import resolve_provider_chain, resolve_vision_chain
from infrastructure.ai.prompt_engineer import call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import json_tokens, material_budget
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.assets.write_protection import user_asset_lock
from infrastructure.correlation import begin_local_scope
from infrastructure.database import SESSION_LOCAL
from infrastructure.library_visibility import library_work
from infrastructure.logger import get_logger
from infrastructure.text import format_local_iso, resolve_prompt_text, utc_now
from pydantic import JsonValue
from runtime.background import TaskScope
from runtime.paid_work import require_new_generation_call
from runtime.user_maintenance import user_request_scope
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.visuals_companion import DEFAULT_TARGET, ComboAssemblyError, load_visual_snapshot

logger = get_logger(__name__)
_TASKS = TaskScope[int]("visuals.stage_preparation")
_STOPPING = False
_JOB_POLL = timedelta(minutes=1)

_INSTRUCTIONS = {
    "zh": (
        "你正在以当前角色的身份准备可以公开展示的完整画面。根据性格、当前活动、已确认安排、"
        "作息与已有作品，判断现在是否值得制作。可以继续使用作品、等待情境变化，或给出一个复查时间。"
        "库存缺少某种组合本身不是制作理由。不为尚未确定的安排编造地点或已发生的活动。"
        "如果确有需要，自行决定制作几项。每项从给出的环境和动作中选择一个，使用当前造型，"
        "在 requirements 里描述实际可见的构图、动作、光线和用途。一个有意义的瞬间可用图片，"
        "只有运动、连续反应或自然循环确有价值才用视频。同一用途的多个版本须有可见差异，"
        "已有或在途的同等画面不重复制作。submit_at 用含时区的时间，null 表示可现在开始。"
        "review_at 表示何时再次评估，null 表示等情境变化。"
        "需要从安睡接续起身时，用 opening 引用目录标明可接续的安睡视频版本，"
        "或引用本计划中较早登记的同环境安睡视频；其余情况留空。只输出符合契约的决定。"
    ),
    "en": (
        "As this character, decide whether to prepare complete visuals that can be shown publicly. "
        "Use the character traits, current activity, confirmed arrangements, routine, and existing work. "
        "You may reuse existing work, wait for a change, or name a time to review. A missing combination alone "
        "does not justify a creation. Do not invent locations or narrate uncertain plans as completed events. "
        "Decide how many items are worthwhile. Choose a supplied setting and action for each item in the adopted "
        "outfit. Describe the composition, motion, light and use in requirements. Use an image for a single "
        "meaningful moment, or video when motion, a response, or a natural loop adds value. Variations for one "
        "purpose should be visibly distinct. Reuse equivalent finished or pending work. "
        "submit_at includes a timezone or is null to start now. review_at schedules reconsideration or is null "
        "to wait for changed circumstances. For a continuous wake-up, use opening to reference an approved sleep "
        "version marked as usable, or an earlier same-setting sleep video in this plan. Otherwise leave it null. "
        "Return only the structured decision."
    ),
}


@dataclass(frozen=True, slots=True)
class _Context:
    basis: str
    identity: VisualIdentitySnapshot
    outfit: OutfitSnapshot
    target: tuple[int, int]
    language: str
    scene_title: str
    upcoming: list[dict[str, str]]
    scenes: list[ScenePlanningMaterial]
    actions: list[ActionPlanningMaterial]
    data: dict[str, JsonValue]


class _PreparationChanged(RuntimeError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _context(db: AsyncSession, user_id: int) -> _Context | None:
    if not await user_is_active(db, user_id) or user_id not in await onboarded_user_ids(db, (user_id,)):
        return None
    prefs = await creation_preferences(db, user_id)
    if not prefs.autonomous_enabled:
        return None
    intent = project_stage_intent(await get_life_state_view(db, user_id) or empty_state_view(user_id))
    adopted = await read_current_outfit_reference(db, user_id)
    identity = await load_visual_identity(db, user_id)
    outfit = (
        await ready_outfit_snapshot(db, user_id, adopted.material_id)
        if adopted and adopted.material_id is not None
        else await initial_outfit_snapshot(db, user_id)
    )
    persona = await planning_persona(db, user_id)
    if identity is None or outfit is None or not existing_asset(outfit.fullbody_url) or persona is None:
        return None
    now = utc_now()
    rhythm = await read_rhythm(db, user_id, now=now)
    routine = await rhythm_prompt_context(db, user_id)
    upcoming = await upcoming_shared_visual_context(db, user_id, now)
    language = await get_user_setting(db, user_id, "language")
    scenes = [row for row in await ready_scene_planning_materials(db, user_id) if existing_asset(row.media_path)]
    actions = await approved_action_planning_materials(db, user_id)
    target = (prefs.target_width, prefs.target_height) if prefs.target_width and prefs.target_height else DEFAULT_TARGET
    data = {
        "character": {
            key: value[:350]
            for key, value in persona.definition.items()
            if key in {"name", "biological_type", "personality", "occupation", "hobbies", "setting"}
        },
        "current": {
            "activity": intent.activity[:350],
            "activity_kind": intent.activity_kind,
            "activity_type": intent.activity_type,
            "scene": intent.scene_title[:150],
            "scene_tags": intent.scene_tags,
        },
        "routine": {
            "timezone": rhythm.timezone,
            "bedtime": routine.usual_bedtime,
            "rise_time": routine.usual_rise_time,
        },
        "outfit": {"id": str(outfit.id), "name": outfit.name, "description": (outfit.description or "")[:350]},
    }
    basis = _digest(
        {
            **{key: data[key] for key in ("character", "current", "routine")},
            "upcoming": upcoming,
            "identity": identity.model_dump(mode="json"),
            "outfit": (str(outfit.id), outfit.fullbody_url),
            "scenes": [(scene.prompt_data(), scene.media_path) for scene in scenes],
            "actions": [(action.id, action.updated_at.isoformat()) for action in actions],
            "target": target,
            "rhythm_state": rhythm.state,
            "rhythm_transition": format_local_iso(rhythm.next_change_at, rhythm.timezone),
        }
    )
    return _Context(
        basis,
        identity,
        outfit,
        target,
        language if isinstance(language, str) else "",
        intent.scene_title,
        upcoming,
        scenes,
        actions,
        {
            **data,
            "local_time": format_local_iso(now, rhythm.timezone) or now.isoformat(),
            "rhythm": {
                "state": rhythm.state,
                "next_change_at": format_local_iso(rhythm.next_change_at, rhythm.timezone),
            },
            "target": list(target),
        },
    )


async def _stock(
    db: AsyncSession, user_id: int, context: _Context, *, opening: InventoryOpening | None = None
) -> list[PreparationStock]:
    rows = await preparation_stock(
        db,
        user_id,
        outfit_path=context.outfit.fullbody_url,
        identity_id=context.identity.avatar_id,
        identity_revision=context.identity.revision,
        target=context.target,
        visual_id=opening.visual_id if opening else None,
        version_number=opening.version if opening else None,
    )
    current_paths: dict[int, str | None] = {}
    stock: list[PreparationStock] = []
    for row in rows:
        scene_id = row.source.scene.scene_id
        if scene_id is None:
            continue
        if scene_id not in current_paths:
            scene = await scene_snapshot(db, user_id, scene_id)
            current_paths[scene_id] = scene.media_path if scene and scene.status == "ready" else None
        if row.source.scene.path == current_paths[scene_id] and existing_asset(row.source.scene.path):
            stock.append(row)
    return stock


def _stock_payload(row: PreparationStock) -> dict[str, JsonValue]:
    source = row.source
    actual = source.media_semantics
    return {
        "id": str(row.visual_id),
        "version": row.version,
        "media_kind": row.kind,
        "scene_id": str(source.scene.scene_id),
        "scene": source.scene.title[:80],
        "action_id": str(source.action.action_id),
        "action": source.action.name[:80],
        "activity": actual.activity_type if actual else None,
        "day_phase": actual.day_phase if actual else None,
        "description": row.description[:220],
        "sleep_opening": row.first_frame() is not None,
    }


def _fit(groups: dict[str, list[dict[str, JsonValue]]], budget: int) -> dict[str, list[dict[str, JsonValue]]]:
    selected: dict[str, list[dict[str, JsonValue]]] = {key: [] for key in groups}
    budget -= json_tokens(selected)
    for batch in zip_longest(*groups.values()):
        for key, row in zip(groups, batch, strict=True):
            if row is not None and (cost := json_tokens(row) + 2) <= budget:
                selected[key].append(row)
                budget -= cost
    return selected


async def _snapshot(db: AsyncSession, user_id: int, context: _Context, item: PreparationRequest) -> VisualSnapshot:
    return await load_visual_snapshot(
        db,
        user_id,
        outfit_id=context.outfit.id,
        scene_id=item.scene_id,
        action_id=item.action_id,
        requirements=item.requirements,
        target_width=context.target[0],
        target_height=context.target[1],
    )


async def _decide(user_id: int, context: _Context, previous: SavedPreparation | None) -> PreparationDecision | None:
    if not context.scenes or not context.actions:
        return None
    config = await load_user_llm_config(user_id, AiTaskKey.proactive)
    if not config.is_configured:
        return None
    async with SESSION_LOCAL() as db:
        images = await resolve_image_gen_chain(db, user_id, task=AiTaskKey.visuals, has_reference=True)
        videos = await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.visuals)
        if not images.providers or not await resolve_vision_chain(db, user_id, task=AiTaskKey.visuals):
            return None
        stock = await _stock(db, user_id, context)
        work = await recent_preparation_work(db, user_id, outfit_id=context.outfit.id)
    payload = {**context.data, "media_kinds": ["image", "video"] if videos else ["image"]}
    instructions = resolve_prompt_text(_INSTRUCTIONS, context.language)
    schema = PreparationChoice.model_json_schema()
    upcoming_scene_ids = {row.get("scene_id") for row in context.upcoming}
    upcoming_action_ids = {row.get("action_id") for row in context.upcoming}
    scenes = sorted(
        context.scenes,
        key=lambda scene: (
            scene.title.strip().casefold() != context.scene_title.strip().casefold(),
            str(scene.id) not in upcoming_scene_ids,
        ),
    )
    actions = sorted(context.actions, key=lambda action: str(action.id) not in upcoming_action_ids)
    stock.sort(key=lambda row: row.source.scene.title.strip().casefold() != context.scene_title.strip().casefold())
    groups = {
        "scenes": [row.prompt_data() for row in scenes],
        "actions": [row.prompt_data() for row in actions],
        "upcoming": [dict(row) for row in context.upcoming],
        "stock": [_stock_payload(row) for row in stock],
        "work": work,
        "previous_plan": [
            {
                "scene_id": str(item.request.scene_id),
                "action_id": str(item.request.action_id),
                "kind": item.request.media_kind,
                "requirements": item.request.requirements[:300],
                "submit_at": item.request.submit_at.isoformat() if item.request.submit_at else None,
            }
            for item in previous.decision.items
            if item.job_id is None and not item.skipped
        ]
        if previous
        else [],
    }
    selected = _fit(groups, material_budget(config.chain, instructions, payload, schema))
    if not selected["scenes"] or not selected["actions"]:
        return None
    scene_ids = {row["id"] for row in selected["scenes"]}
    action_ids = {row["id"] for row in selected["actions"]}
    stock_choices = {(row["id"], row["version"]): row for row in selected["stock"]}

    def parse(value: JsonValue) -> PreparationChoice:
        choice = PreparationChoice.model_validate(value)
        if choice.review_at is not None and choice.review_at <= utc_now():
            raise ValueError("A review time must be in the future")
        for item in choice.items:
            if (
                str(item.scene_id) not in scene_ids
                or str(item.action_id) not in action_ids
                or (item.media_kind == "video" and not videos)
            ):
                raise ValueError("Use available materials and media types")
            if isinstance(item.opening, InventoryOpening):
                opening = stock_choices.get((str(item.opening.visual_id), item.opening.version))
                if not opening or opening["scene_id"] != str(item.scene_id) or not opening["sleep_opening"]:
                    raise ValueError("The opening must be a supplied sleep version in the same setting")
        if any(str(identifier) not in {key[0] for key in stock_choices} for identifier in choice.reuse_visual_ids):
            raise ValueError("Reuse a version supplied in stock")
        return choice

    async def before_call() -> None:
        require_new_generation_call(user_id)
        async with SESSION_LOCAL() as db:
            if not (await creation_preferences(db, user_id)).autonomous_enabled:
                raise VisualStateError("伙伴自主制作已关闭")

    choice = await call_llm_json(
        config,
        instructions,
        {**payload, **selected},
        schema=schema,
        schema_name="visual_stage_preparation",
        parse_output=parse,
        repair_prompt=(
            "Correct only structure. Preserve the decision to create or reuse, item keys, materials, "
            "requirements, prerequisites, and all planned times."
        ),
        before_call=before_call,
    )
    items: list[PreparationItem] = []
    async with user_asset_lock(user_id), SESSION_LOCAL() as db:
        for request in choice.items:
            snapshot = await _snapshot(db, user_id, context, request)
            items.append(PreparationItem(request=request, snapshot=snapshot))
    return PreparationDecision(
        id=uuid4().hex,
        reason=choice.reason,
        reuse_visual_ids=choice.reuse_visual_ids,
        items=items,
        review_at=choice.review_at,
    )


def _material_basis(snapshot: VisualSnapshot) -> tuple:
    source = snapshot.source
    if not isinstance(source, CinematicSource):
        raise ValueError("A complete-scene preparation must freeze its environment")
    return (
        source.identity,
        source.identity_hash,
        source.portrait_hash,
        source.identity_reference_role,
        source.outfit.content_hash,
        source.scene.content_hash,
        source.action.media_basis(),
        snapshot.target_width,
        snapshot.target_height,
    )


async def _job_for_item(user_id: int, plan: PreparationDecision, item: PreparationItem) -> VisualJobView | None:
    if item.job_id is not None:
        try:
            return await visual_job_detail(user_id, item.job_id)
        except VisualNotFoundError:
            return None
    registered = await creation_request(user_id, f"stage-preparation:{plan.id}:{item.request.key}")
    return registered[0] if registered else None


async def _opening(
    user_id: int, context: _Context, plan: PreparationDecision, item: PreparationItem
) -> tuple[ActorOpeningFrame | None, bool]:
    reference = item.request.opening
    if reference is None:
        return None, False
    if isinstance(reference, PlannedOpening):
        dependency = next(candidate for candidate in plan.items if candidate.request.key == reference.item)
        reused = dependency.reused_opening
        if reused is None:
            job = await _job_for_item(user_id, plan, dependency)
            if job is None or job.state in {"queued", "running", "paused", "unknown"}:
                return None, not dependency.skipped
            if job.state != "succeeded" or job.result_visual_id is None:
                return None, False
            async with SESSION_LOCAL() as db:
                version = await preparation_result_version(db, user_id, job.id)
            if version is None:
                return None, False
            reused = InventoryOpening(kind="inventory", visual_id=job.result_visual_id, version=version)
        async with SESSION_LOCAL() as db:
            rows = await _stock(db, user_id, context, opening=reused)
    else:
        async with SESSION_LOCAL() as db:
            rows = await _stock(db, user_id, context, opening=reference)
    frozen = item.snapshot.source
    if not isinstance(frozen, CinematicSource):
        raise ValueError("A wake-up must have a frozen environment")
    for row in rows:
        if (
            row.source.scene.content_hash == frozen.scene.content_hash
            and (opening := row.first_frame())
            and existing_asset(opening.path)
        ):
            return opening, False
    return None, False


async def _submit(
    user_id: int, context: _Context, saved: SavedPreparation, item: PreparationItem
) -> Literal["done", "waiting", "stale", "changed"]:
    plan = saved.decision
    if job := await _job_for_item(user_id, plan, item):
        item.job_id = job.id
        return "done"
    opening, waiting = await _opening(user_id, context, plan, item)
    if waiting:
        return "waiting"
    if item.request.opening is not None and opening is None:
        item.skipped = True
        return "done"

    async def verify_admission(db: AsyncSession) -> None:
        refreshed = await _context(db, user_id)
        if refreshed is None or refreshed.basis != context.basis:
            raise _PreparationChanged
        # 持锁直至工作项提交，已开始等待的上下文写入只能排在此次受理之后。
        if not await lock_current_preparation(db, user_id, saved):
            raise _PreparationChanged

    try:
        async with user_asset_lock(user_id), SESSION_LOCAL() as db:
            current = await _snapshot(db, user_id, context, item.request)
            if _material_basis(current) != _material_basis(item.snapshot):
                item.skipped = True
                return "stale"
            source = item.snapshot.source
            if not isinstance(source, CinematicSource):
                raise ValueError("A complete-scene preparation must have a frozen environment")
            if "waking" in source.action.semantic_tags and opening is None:
                item.skipped = True
                return "done"
            if opening:
                source = source.model_copy(update={"opening_frame": opening})
            stock = await _stock(db, user_id, context)
            for row in stock:
                if (
                    row.kind == item.request.media_kind
                    and row.source.scene.content_hash == source.scene.content_hash
                    and row.source.action.media_basis() == source.action.media_basis()
                    and row.source.requirements.strip() == source.requirements.strip()
                    and row.source.opening_frame == source.opening_frame
                ):
                    if row.first_frame() is not None:
                        item.reused_opening = InventoryOpening(
                            kind="inventory", visual_id=row.visual_id, version=row.version
                        )
                    item.skipped = True
                    return "done"
            await db.commit()
            job = await create_visual_job(
                user_id,
                snapshot=item.snapshot.model_copy(update={"source": source}),
                target_kind=item.request.media_kind,
                source="stage_preparation",
                request_id=f"stage-preparation:{plan.id}:{item.request.key}",
                verify_admission=verify_admission,
            )
            item.job_id = job.id
    except _PreparationChanged:
        return "changed"
    except ComboAssemblyError:
        item.skipped = True
        return "stale"
    return "done"


async def _store(user_id: int, prior: SavedPreparation | None, value: SavedPreparation) -> bool:
    async with SESSION_LOCAL() as db:
        saved = await save_preparation(db, user_id, value, expected_revision=prior.revision if prior else None)
        await db.commit()
    return saved


async def _run(user_id: int) -> None:
    begin_local_scope()
    async with user_request_scope(user_id) as acquired:
        if acquired:
            with library_work(None):
                await _reconcile(user_id)


async def _reconcile(user_id: int) -> None:
    async with SESSION_LOCAL() as db:
        saved = await saved_preparation(db, user_id)
        input_revision = await preparation_input_revision(db, user_id)
        context = await _context(db, user_id)
    if context is None:
        return
    now = utc_now()
    retry_review = False
    if (
        saved is None
        or saved.basis != context.basis
        or (saved.decision.review_at is not None and saved.decision.review_at <= now)
    ):
        try:
            choice = await _decide(user_id, context, saved)
        except (LLMRuntimeError, ComboAssemblyError):
            logger.warning("Complete-scene preparation decision unavailable", extra={"user_id": user_id}, exc_info=True)
            choice = None
        if choice is None:
            if saved is None:
                return
            if saved.basis != context.basis:
                await _store(
                    user_id,
                    saved,
                    replace(saved, revision=saved.revision + 1, input_revision=input_revision, due_at=now + _JOB_POLL),
                )
                return
            retry_review = True
        else:
            async with SESSION_LOCAL() as db:
                input_revision = await preparation_input_revision(db, user_id)
                refreshed = await _context(db, user_id)
            if refreshed is None or refreshed.basis != context.basis:
                schedule_stage_preparation(user_id)
                return
            current = SavedPreparation(saved.revision + 1 if saved else 1, input_revision, context.basis, choice, now)
            if not await _store(user_id, saved, current):
                return
            saved = current
    if saved.input_revision != input_revision:
        current = replace(saved, revision=saved.revision + 1, input_revision=input_revision)
        if not await _store(user_id, saved, current):
            return
        saved = current
    if saved.due_at is None or saved.due_at > now:
        return

    async with SESSION_LOCAL() as db:
        active = await active_preparation_job(db, user_id)
    plan = saved.decision
    review_at = now + _JOB_POLL if retry_review else plan.review_at
    deadlines = [review_at] if review_at is not None else []
    submitted = active
    for item in plan.items:
        if item.job_id is not None or item.skipped:
            continue
        submit_at = item.request.submit_at
        if submit_at is not None and submit_at > now:
            deadlines.append(submit_at)
            continue
        if submitted:
            deadlines.append(now + _JOB_POLL)
            continue
        status = await _submit(user_id, context, saved, item)
        if status == "changed":
            schedule_stage_preparation(user_id)
            return
        if status == "waiting":
            deadlines.append(now + _JOB_POLL)
        elif status == "stale":
            plan.review_at = now
            deadlines.append(now + _JOB_POLL)
        else:
            submitted = item.job_id is not None
    await _store(
        user_id,
        saved,
        replace(saved, revision=saved.revision + 1, due_at=min(deadlines) if deadlines else None),
    )


def schedule_stage_preparation(user_id: int) -> None:
    if not _STOPPING:
        _TASKS.schedule(user_id, lambda: _run(user_id), name=f"visuals.stage_preparation.{user_id}", user_id=user_id)


async def queue_stage_preparation(user_id: int) -> None:
    schedule_stage_preparation(user_id)


async def start_stage_preparation() -> None:
    global _STOPPING
    _STOPPING = False


async def stop_stage_preparation() -> None:
    global _STOPPING
    _STOPPING = True
    await _TASKS.drain()
