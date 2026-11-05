"""一次推进一个持久生活步骤；业务模块拥有实际制作与采纳。"""

import asyncio
from datetime import UTC, datetime, timedelta

from features.action_library.queries import action_detail
from features.appearance.outfit_jobs import create_outfit_job, run_outfit_job
from features.automation.cron_jobs import create_job_in_session
from features.configuration.settings.values import get_user_setting
from features.life.actions import (
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
from features.scenes.service import SceneBusyError, SceneLockedError, SceneQuotaExceededError
from features.scenes.snapshots import schedule_scene_snapshot
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import utc_now
from infrastructure.timezone import parse_timezone
from runtime.memory_scope import MemoryScope
from runtime.user_maintenance import user_request_scope

from workflows.life_capabilities import action_block_reason
from workflows.life_reading import read_content
from workflows.nightly.window import in_nightly_window
from workflows.visuals_companion import design_action_reference, generate_visual_reference

logger = get_logger(__name__)
_LOCKS: dict[int, asyncio.Lock] = {}
_CONCURRENCY = asyncio.Semaphore(4)


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
            if problem := await action_block_reason(user_id, step):
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
