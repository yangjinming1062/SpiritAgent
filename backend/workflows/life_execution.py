"""一次推进一个持久生活步骤；业务模块拥有实际制作与采纳。"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from features.accounts.queries import nightly_account, user_is_active
from features.action_library.queries import action_detail, list_actions
from features.appearance.identity import IDENTITY_NOT_CONFIRMED, load_visual_identity
from features.appearance.outfit_jobs import create_outfit_job, run_outfit_job
from features.appearance.queries import ready_outfit_snapshots
from features.automation.cron_jobs import create_job_in_session
from features.companion.persona_service import onboarded_user_ids
from features.companion.visual_state import persona_snapshot
from features.configuration.settings.values import get_user_setting, load_user_settings
from features.life.actions import (
    LIFE_CAPABILITIES,
    DesignAction,
    OutfitAction,
    OutreachAction,
    ReadingAction,
    ReadingResult,
    SceneAction,
    VisualAction,
)
from features.life.execution import (
    checkpoint,
    claim_step,
    defer_step,
    finish_step,
    mark_step_submitted,
    pending_step,
    reconcile_submission,
)
from features.life.schemas import LifeStepRequest, LifeStepView
from features.life.use_cases import read_step, require_step_submission, update_business_steps_in_session
from features.media.image_generation import resolve_image_gen_chain
from features.scenes.service import SceneBusyError, SceneLockedError, SceneQuotaExceededError
from features.scenes.snapshots import list_scene_snapshots, schedule_scene_snapshot
from features.topics.store import load_prompt_topics
from features.visuals.queries import presentation_state
from infrastructure.ai.llm_client import resolve_provider_chain
from infrastructure.ai.models import ModelModality
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from infrastructure.timezone import parse_timezone
from infrastructure.web.extract_provider import resolve_extract_provider
from runtime.memory_scope import MemoryScope
from runtime.tools.toolsets import disabled_backend_tool_names
from runtime.user_maintenance import user_request_scope

from workflows.life_reading import read_content
from workflows.nightly.window import in_nightly_window
from workflows.visuals_companion import design_action_reference, generate_visual_reference

logger = get_logger(__name__)
_LOCKS: dict[int, asyncio.Lock] = {}
_CONCURRENCY = asyncio.Semaphore(4)


@dataclass(frozen=True)
class CapabilityBlock:
    disposition: Literal["permission_required", "unavailable"]
    reason: str


async def execution_context(user_id: int, *, source: str = "life") -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        outfits = await ready_outfit_snapshots(db, user_id)
        scenes = (await list_scene_snapshots(db, user_id, ready_only=True, limit=50)).scenes
        actions = (await list_actions(db, user_id, status="approved", limit=50)).actions
        topics = await load_prompt_topics(db, MemoryScope(user_id, "companion"), utc_now())
    blocked = await capability_blocks(user_id, source=source)
    if not (outfits and scenes and actions):
        blocked.setdefault("visual.generate", CapabilityBlock("unavailable", "缺少可用的造型、环境或动作"))
    return {
        "capabilities": {name: name not in blocked for name in LIFE_CAPABILITIES},
        "blocked_capabilities": {name: problem.reason for name, problem in blocked.items()},
        "capability_descriptions": {
            name: definition.description for name, definition in LIFE_CAPABILITIES.items() if name not in blocked
        },
        "outfits": [{"id": str(item.id), "name": item.name, "description": item.description} for item in outfits],
        "scenes": [{"id": str(item.id), "title": item.title, "description": item.description} for item in scenes],
        "actions": [
            {"id": str(item.id), "name": item.name, "description": item.motion_description} for item in actions
        ],
        "topics": [
            {"id": str(item.topic_id), "title": item.title, "summary": item.summary, "url": item.url} for item in topics
        ],
    }


async def capability_blocks(
    user_id: int, *, source: str = "life", capabilities: tuple[str, ...] | None = None
) -> dict[str, CapabilityBlock]:
    """规划目录与执行前复核共用政策、身份、开关和模型资格。"""
    targets = set(LIFE_CAPABILITIES if capabilities is None else capabilities)
    async with SESSION_LOCAL() as db:
        if not await user_is_active(db, user_id) or user_id not in await onboarded_user_ids(db, (user_id,)):
            return dict.fromkeys(targets, CapabilityBlock("unavailable", "账户未激活或伙伴引导尚未完成"))
        identity_ready = await load_visual_identity(db, user_id) is not None
        persona = await persona_snapshot(db, user_id)
        disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
        if source == "nightly":
            account = await nightly_account(db, user_id)
            if account is None or not account.nightly_activity_enabled:
                return dict.fromkeys(targets, CapabilityBlock("permission_required", "夜间自主活动已关闭"))
    if persona is None:
        return dict.fromkeys(targets, CapabilityBlock("unavailable", "伙伴资料尚未就绪"))
    planning_task = AiTaskKey.nightly if source == "nightly" else AiTaskKey.proactive
    if not (await load_user_llm_config(user_id, planning_task)).is_configured:
        return dict.fromkeys(targets, CapabilityBlock("unavailable", "生活判断模型尚未配置"))
    blocked: dict[str, CapabilityBlock] = {}
    if not identity_ready:
        blocked.update(
            dict.fromkeys(
                ("outfit.create", "visual.generate", "action.design"),
                CapabilityBlock("unavailable", IDENTITY_NOT_CONFIRMED),
            )
        )
    if persona.outfit_policy == "locked":
        blocked["outfit.create"] = CapabilityBlock("permission_required", "自主添置造型已关闭")
    if persona.scene_policy == "locked" or "scene_create" in disabled:
        blocked["scene.create"] = CapabilityBlock("permission_required", "自主添置环境已关闭")
    if not SETTINGS.action_autocreate_enabled:
        blocked["action.design"] = CapabilityBlock("permission_required", "自主设计动作已关闭")
    if "visual.generate" in targets and not (await presentation_state(user_id, with_catalog=False)).autonomous_enabled:
        blocked["visual.generate"] = CapabilityBlock("permission_required", "自主影像制作已关闭")
    if source != "nightly":
        blocked["outreach.schedule"] = CapabilityBlock("permission_required", "后续联系由夜间规划安排")
    async with SESSION_LOCAL() as db:
        for capability, task in (
            ("outfit.create", AiTaskKey.outfit),
            ("scene.create", AiTaskKey.scene),
            ("visual.generate", AiTaskKey.visuals),
        ):
            if capability in targets and capability not in blocked:
                chain = await resolve_image_gen_chain(
                    db, user_id, task=task, has_reference=capability != "scene.create"
                )
                if not chain.providers:
                    blocked[capability] = CapabilityBlock("unavailable", "图片制作模型尚未配置")
    if (
        "action.design" in targets
        and "action.design" not in blocked
        and not (
            await load_user_llm_config(
                user_id, AiTaskKey.visuals, required_modalities=frozenset({ModelModality.text, ModelModality.image})
            )
        ).is_configured
    ):
        blocked["action.design"] = CapabilityBlock("unavailable", "动作评审模型尚未配置")
    return {name: problem for name, problem in blocked.items() if name in targets}


async def _block_reason(user_id: int, step: LifeStepView) -> tuple[str, str] | None:
    if problem := (await capability_blocks(user_id, source=step.source, capabilities=(step.kind,))).get(step.kind):
        return problem.disposition, problem.reason
    if isinstance(step.action, ReadingAction):
        async with SESSION_LOCAL() as db:
            disabled = disabled_backend_tool_names(await load_user_settings(db, user_id))
        if (step.action.query and "web_search" in disabled) or (
            (step.action.query or step.action.urls) and "web_extract" in disabled
        ):
            return "permission_required", "网页读取工具已关闭"
        if step.action.query or step.action.urls:
            provider = resolve_extract_provider()
            if not provider.is_available() or not provider.supports_extract():
                return "unavailable", "网页正文提取尚未配置"
    if isinstance(step.action, VisualAction) and step.action.media_kind == "video":
        async with SESSION_LOCAL() as db:
            if not await resolve_provider_chain(db, user_id, "video_gen", task=AiTaskKey.visuals):
                return "unavailable", "视频制作模型尚未配置"
    return None


async def _execute(user_id: int, step: LifeStepView) -> None:
    action = step.action
    request = LifeStepRequest(
        idempotency_key=step.idempotency_key,
        kind=step.kind,
        description=step.description,
        narrative=step.narrative,
        pursuit_id=step.pursuit_id,
    )
    if isinstance(action, VisualAction):
        await generate_visual_reference(
            user_id, request_id=step.idempotency_key, life_step=request, **action.model_dump(exclude={"capability"})
        )
    elif isinstance(action, SceneAction):
        await schedule_scene_snapshot(user_id, origin="llm", notes=action.notes, life_step=request)
    elif isinstance(action, OutfitAction):
        job = await create_outfit_job(user_id, action.description, request)
        await run_outfit_job(user_id, job.id)
    elif isinstance(action, DesignAction):
        await design_action_reference(user_id, life_step=request, **action.model_dump(exclude={"capability"}))
    elif isinstance(action, ReadingAction):
        await mark_step_submitted(user_id, step.id)
        reading = await read_content(user_id, step)
        await _finish_reading(user_id, step.id, reading)
    elif isinstance(action, OutreachAction):
        await _schedule_outreach(user_id, step, request, action)
    else:
        raise ValueError("不支持的生活能力")


async def _schedule_outreach(
    user_id: int, step: LifeStepView, request: LifeStepRequest, action: OutreachAction
) -> None:
    now = utc_now()
    run_at = max(action.scheduled_at, now + timedelta(minutes=2)).astimezone(UTC)
    if run_at >= action.expires_at:
        raise ValueError("联系安排已过期")
    async with SESSION_LOCAL() as db:
        await require_step_submission(db, user_id, request)
        job = await create_job_in_session(
            db,
            MemoryScope(user_id, "companion"),
            action.prompt,
            f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *",
            action.name,
            one_shot=True,
            kind="special",
            expires_at=action.expires_at,
            next_run_at=run_at,
        )
        await update_business_steps_in_session(
            db,
            user_id,
            kind=step.kind,
            business_ref={"step_id": step.id},
            state="completed",
            result_ref={"cron_job_id": job["id"]},
        )
        await db.commit()


async def _finish_reading(user_id: int, step_id: int, reading: ReadingResult) -> None:
    await finish_step(
        user_id,
        step_id,
        state="completed",
        result={
            "reading_step_id": step_id,
            "notes": reading.notes,
            "sources": [source.model_dump(mode="json", exclude={"content"}) for source in reading.sources],
        },
        reading=reading,
    )


async def _reconcile(user_id: int, step: LifeStepView) -> None:
    if step.kind == "outfit.create" and step.business_ref.get("job_id"):
        await run_outfit_job(user_id, int(step.business_ref["job_id"]))
    elif step.kind == "action.design" and step.business_ref.get("action_id"):
        async with SESSION_LOCAL() as db:
            action = await action_detail(db, user_id, int(step.business_ref["action_id"]))
        if action is None:
            await finish_step(user_id, step.id, state="failed", error="原动作已删除")
        elif action.status in {"approved", "rejected", "deferred", "failed"}:
            await finish_step(
                user_id,
                step.id,
                state="completed" if action.status == "approved" else "failed",
                result={"action_id": action.id} if action.status == "approved" else {},
                error=None if action.status == "approved" else action.review_reason,
            )
    elif step.business_ref.get("job_id"):
        # 媒体模块恢复原任务，并在其采纳事务内结算；此处不发起第二次制作。
        return
    elif step.kind == "content.read":
        saved = await checkpoint(user_id, step.id)
        if saved.get("notes") and saved.get("sources"):
            reading = ReadingResult(sources=saved["sources"], notes=saved["notes"])
            await _finish_reading(user_id, step.id, reading)
        elif step.state == "running":
            await finish_step(user_id, step.id, state="unknown", error="阅读已提交但结果未保存，须核对原结果")
    elif step.state == "running":
        await finish_step(user_id, step.id, state="unknown", error="执行中断且尚无业务受理记录，须核对原结果")


async def run_life_execution(user_id: int, *, step_id: int | None = None) -> None:
    async with user_request_scope(user_id) as acquired:
        if not acquired:
            return
        async with _LOCKS.setdefault(user_id, asyncio.Lock()), _CONCURRENCY:
            step = await pending_step(user_id, step_id)
            if step is None or step.state in {"completed", "failed", "cancelled"}:
                return
            step = await reconcile_submission(user_id, step.id)
            if step is None:
                return
            if step.state in {"running", "unknown"}:
                try:
                    await _reconcile(user_id, step)
                except Exception as exc:
                    if step.kind == "content.read":
                        await finish_step(user_id, step.id, state="failed", error=str(exc))
                    raise
                return
            if problem := await _block_reason(user_id, step):
                disposition, reason = problem
                if step.source == "nightly":
                    await finish_step(user_id, step.id, state="cancelled", error=reason)
                else:
                    await defer_step(user_id, step.id, reason, disposition=disposition)
                return
            async with SESSION_LOCAL() as db:
                timezone = parse_timezone(await get_user_setting(db, user_id, "timezone"))
            if timezone is None:
                await defer_step(user_id, step.id, "尚未设置时区", disposition="unavailable")
                return
            if step.source == "nightly":
                saved = await checkpoint(user_id, step.id)
                deadline = saved.get("expires_at")
                if not in_nightly_window(utc_now(), timezone) or (
                    deadline and datetime.fromisoformat(deadline) <= utc_now()
                ):
                    await finish_step(user_id, step.id, state="cancelled", error="原夜间窗口已结束，未提交的步骤不补跑")
                    return
            claimed = await claim_step(user_id, step.id, timezone)
            if claimed is None:
                return
            try:
                await _execute(user_id, claimed)
            except (SceneBusyError, SceneLockedError, SceneQuotaExceededError) as exc:
                await defer_step(
                    user_id,
                    step.id,
                    str(exc),
                    disposition="permission_required" if isinstance(exc, SceneLockedError) else "waiting",
                    unsubmitted=True,
                )
            except asyncio.CancelledError:
                await finish_step(user_id, step.id, state="unknown", error="执行中断，保留原任务待核对")
                raise
            except Exception as exc:
                logger.warning("life action failed", extra={"user_id": user_id, "step_id": step.id}, exc_info=True)
                latest = await read_step(user_id, step.id)
                if latest is not None and latest.state == "running" and not latest.business_ref:
                    await finish_step(
                        user_id,
                        step.id,
                        state="failed" if step.kind == "content.read" or isinstance(exc, ValueError) else "unknown",
                        error=str(exc),
                    )
