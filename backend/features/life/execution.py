"""生活步骤的持久认领、共享额度和执行检查点。外部调用始终在事务外。"""

from copy import deepcopy
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import ensure_utc, utc_now
from runtime.events.emit import emit_ws_event
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.jobs.service import requested_job_id

from .actions import CREATION_CAPABILITIES, LIFE_ACTION, NextAction, ReadingResult
from .models import LifeActionAdmission, LifePursuit, LifeStep
from .queries import step_view
from .schemas import LifeEventInput, LifeStepView
from .service import LifeInputError, clear_activity_step, create_step, get_or_create_state, record_event
from .settlement import settle_business_steps

_RETRY_DELAY = timedelta(minutes=15)


async def enqueue_action(
    db: AsyncSession,
    user_id: int,
    action: NextAction,
    *,
    request_id: str,
    pursuit_id: int | None,
    source: str = "life",
    expires_at: datetime | None = None,
) -> LifeStepView:
    if not action.narrative or not action.narrative.strip():
        raise LifeInputError("登记生活行动必须提供伙伴叙事")
    row = await create_step(
        db,
        user_id,
        idempotency_key=request_id,
        kind=action.action.capability,
        description=action.description,
        narrative=action.narrative,
        pursuit_id=pursuit_id,
    )
    payload = action.action.model_dump(mode="json")
    if row.action_json is not None and row.action_json != payload:
        raise LifeInputError("同一执行请求不能改用不同参数")
    if row.action_json is None:
        if pursuit_id is not None and await db.scalar(
            select(LifeStep.id)
            .where(
                LifeStep.user_id == user_id,
                LifeStep.pursuit_id == pursuit_id,
                LifeStep.id != row.id,
                LifeStep.state.in_(("planned", "running", "unknown")),
            )
            .limit(1)
        ):
            raise LifeInputError("事项已有未解决步骤，不能重复登记下一步")
        row.action_json, row.source = payload, source
        if expires_at is not None:
            row.checkpoint = {"expires_at": expires_at.isoformat()}
        row.not_before = action.not_before or utc_now()
        if pursuit_id is not None:
            last_failure = await db.scalar(
                select(func.max(LifeStep.finished_at)).where(
                    LifeStep.user_id == user_id,
                    LifeStep.pursuit_id == pursuit_id,
                    LifeStep.kind == row.kind,
                    LifeStep.state == "failed",
                )
            )
            if last_failure is not None and (retry_at := ensure_utc(last_failure) + _RETRY_DELAY) > ensure_utc(
                row.not_before
            ):
                row.not_before = retry_at
                row.disposition, row.wait_reason, row.failure_code = "waiting", "上次执行失败，稍后再试", "retry_wait"
        row.is_creation = row.kind in CREATION_CAPABILITIES
        await db.flush()
    return step_view(row)


async def reserve_external_action(
    user_id: int, *, request_id: str, kind: str, description: str, narrative: str
) -> LifeStepView:
    async with SESSION_LOCAL() as db:
        if not narrative or not narrative.strip():
            raise LifeInputError("登记生活行动必须提供伙伴叙事")
        row = await create_step(
            db, user_id, idempotency_key=request_id, kind=kind, description=description, narrative=narrative
        )
        row.source = "nightly"
        await db.commit()
        await db.refresh(row)
        return step_view(row)


def _pending_steps(now: datetime) -> Select[tuple[LifeStep]]:
    return (
        select(LifeStep)
        .outerjoin(LifePursuit, LifePursuit.id == LifeStep.pursuit_id)
        .where(
            LifeStep.action_json.is_not(None),
            or_(
                LifeStep.state.in_(("running", "unknown")),
                (LifeStep.state == "planned")
                & or_(LifeStep.not_before.is_(None), LifeStep.not_before <= now)
                & or_(LifeStep.pursuit_id.is_(None), LifePursuit.status == "active"),
            ),
        )
    )


async def execution_user_ids(db: AsyncSession, now: datetime) -> set[int]:
    return set(await db.scalars(_pending_steps(now).with_only_columns(LifeStep.user_id).distinct()))


async def pending_step(user_id: int, step_id: int | None = None) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        if step_id is not None:
            query = select(LifeStep).where(LifeStep.id == step_id, LifeStep.action_json.is_not(None))
        else:
            query = _pending_steps(utc_now()).order_by(
                case((LifeStep.state == "running", 0), (LifeStep.state == "planned", 1), else_=2), LifeStep.id
            )
        row = await db.scalar(query.where(LifeStep.user_id == user_id).limit(1))
        return step_view(row) if row else None


async def checkpoint(user_id: int, step_id: int) -> dict[str, Any]:
    async with SESSION_LOCAL() as db:
        return deepcopy(
            await db.scalar(select(LifeStep.checkpoint).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
            or {}
        )


async def save_checkpoint(user_id: int, step_id: int, **values: Any) -> None:
    async with SESSION_LOCAL() as db:
        await get_or_create_state(db, user_id, lock=True)
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        if row is None or row.state not in {"running", "unknown"}:
            raise LifeInputError("步骤已失效")
        row.checkpoint = {**row.checkpoint, **values}
        await db.commit()


async def defer_step(
    user_id: int,
    step_id: int,
    reason: str,
    *,
    disposition: str = "waiting",
    until: datetime | None = None,
    unsubmitted: bool = False,
) -> None:
    async with SESSION_LOCAL() as db:
        state = await get_or_create_state(db, user_id, lock=True)
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        if row is None:
            return
        if unsubmitted and row.state == "running" and row.job_id is None and row.business_action_id is None:
            submitted = await db.scalar(
                select(LifeActionAdmission.submitted).where(
                    LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id == row.idempotency_key
                )
            )
            if submitted:
                return
            row.state = "planned"
            if state.activity_step_id == row.id:
                clear_activity_step(db, state)
        if row.state != "planned":
            return
        # 等待条件不是失败；failure_code 只保留真实失败类别，供公开投影与模型上下文使用。
        row.disposition, row.wait_reason = disposition, reason
        row.not_before = until or utc_now() + _RETRY_DELAY
        state.revision += 1
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": step_id})
        await db.commit()


async def claim_step(user_id: int, step_id: int, timezone: ZoneInfo) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        state = await get_or_create_state(db, user_id, lock=True)
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        now = utc_now()
        if row is None or row.state != "planned" or (row.not_before and ensure_utc(row.not_before) > now):
            return None
        if row.pursuit_id is not None and not await db.scalar(
            select(LifePursuit.id).where(
                LifePursuit.id == row.pursuit_id, LifePursuit.user_id == user_id, LifePursuit.status == "active"
            )
        ):
            return None
        if await db.scalar(
            select(LifeStep.id)
            .where(LifeStep.user_id == user_id, LifeStep.state == "running", LifeStep.id != step_id)
            .limit(1)
        ):
            return None
        if row.action_json is not None:
            action = LIFE_ACTION.validate_python(row.action_json)
            if action.capability != row.kind:
                raise LifeInputError("步骤与执行能力不一致")
        row.is_creation = row.kind in CREATION_CAPABILITIES
        day = now.astimezone(timezone).date()
        admission = await db.scalar(
            select(LifeActionAdmission).where(
                LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id == row.idempotency_key
            )
        )
        if admission is not None and admission.capability != row.kind:
            raise LifeInputError("同一请求的执行能力已经确定")
        if admission is not None and admission.submitted:
            row.state, row.disposition, row.wait_reason = "unknown", "unknown", "这个请求已经提交过，须核对原结果"
            row.failure_code = "unknown_submission"
            row.budget_date, row.started_at = admission.budget_date, admission.admitted_at
            await db.commit()
            return None
        if admission is None:
            count, creations = (
                await db.execute(
                    select(func.count(), func.count().filter(LifeActionAdmission.is_creation.is_(True)))
                    .select_from(LifeActionAdmission)
                    .where(LifeActionAdmission.user_id == user_id, LifeActionAdmission.budget_date == day)
                )
            ).one()
            if count >= SETTINGS.life_actions_per_day or (
                row.is_creation and creations >= SETTINGS.life_creations_per_day
            ):
                row.disposition, row.wait_reason, row.failure_code = (
                    "waiting",
                    "今日自主执行额度已用完",
                    "quota_exhausted",
                )
                row.not_before = datetime.combine(day + timedelta(days=1), time.min, timezone)
                await db.commit()
                return None
            admission = LifeActionAdmission(
                user_id=user_id,
                request_id=row.idempotency_key,
                capability=row.kind,
                budget_date=day,
                is_creation=row.is_creation,
            )
            db.add(admission)
        row.budget_date = admission.budget_date
        row.state, row.started_at, row.disposition, row.wait_reason = "running", row.started_at or now, "executing", ""
        row.failure_code = None
        row.attempts += 1
        activity_type = "reading" if row.kind == "content.read" else "creating"
        if (state.current_activity, state.activity_kind, state.activity_type) != (
            row.description,
            "execution",
            activity_type,
        ):
            state.presentation_revision += 1
            emit_ws_event(
                db,
                user_id=user_id,
                event_type="companion.life.presentation_requested",
                payload={"revision": state.presentation_revision},
            )
        state.current_activity = row.description
        state.narrative = row.narrative
        state.activity_type = activity_type
        state.activity_kind, state.activity_step_id, state.activity_started_at = "execution", row.id, now
        state.revision += 1
        await record_event(
            db,
            user_id,
            LifeEventInput(
                event_key=f"step:{row.id}:started",
                kind="life.step.started",
                source=row.source,
                fact_type="execution",
                pursuit_id=row.pursuit_id,
                step_id=row.id,
                fact={"description": row.description, "capability": row.kind},
                narrative=row.narrative,
            ),
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
        await db.commit()
        await db.refresh(row)
        return step_view(row)


async def mark_step_submitted(user_id: int, step_id: int) -> None:
    """没有独立业务行的阅读与联系动作，在外部调用前保存提交事实。"""
    async with SESSION_LOCAL() as db:
        await get_or_create_state(db, user_id, lock=True)
        step = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        if step is None or step.state != "running":
            raise LifeInputError("步骤已暂停或结束")
        admission = await db.scalar(
            select(LifeActionAdmission).where(
                LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id == step.idempotency_key
            )
        )
        if admission is None:
            raise LifeInputError("步骤未取得执行额度")
        admission.submitted = True
        await db.commit()


async def reconcile_submission(user_id: int, step_id: int) -> LifeStepView | None:
    async with SESSION_LOCAL() as db:
        state = await get_or_create_state(db, user_id, lock=True)
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        if row is None:
            return None
        admission = await db.scalar(
            select(LifeActionAdmission).where(
                LifeActionAdmission.user_id == user_id, LifeActionAdmission.request_id == row.idempotency_key
            )
        )
        if admission is not None and row.state in {"planned", "running", "unknown"}:
            if row.job_id is None and row.business_action_id is None:
                row.job_id = await requested_job_id(db, user_id, row.idempotency_key)
            if admission.submitted and (
                row.state == "planned"
                or (row.state == "running" and row.job_id is None and row.business_action_id is None)
            ):
                row.state, row.disposition, row.wait_reason = "unknown", "unknown", "这个请求已经提交过，须核对原结果"
                row.failure_code = "unknown_submission"
                row.budget_date, row.started_at = admission.budget_date, admission.admitted_at
            elif (
                row.state in {"running", "unknown"}
                and not admission.submitted
                and row.job_id is None
                and row.business_action_id is None
            ):
                row.state, row.disposition, row.wait_reason = "planned", "waiting", "上次执行尚未提交，继续原请求"
                row.failure_code = "submission_pending"
                if state.activity_step_id == row.id:
                    clear_activity_step(db, state)
            if db.is_modified(row):
                state.revision += 1
                emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": row.id})
                await db.commit()
                await db.refresh(row)
        return step_view(row)


async def finish_step(
    user_id: int,
    step_id: int,
    *,
    state: str,
    result: dict | None = None,
    error: str | None = None,
    reading: ReadingResult | None = None,
) -> None:
    async with SESSION_LOCAL() as db:
        await get_or_create_state(db, user_id, lock=True)
        row = await db.scalar(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id == step_id))
        if row is None:
            return
        if reading is not None and row.reading_result_json is None:
            row.reading_result_json = reading.model_dump(mode="json")
        await settle_business_steps(
            db, user_id, kind=row.kind, business_ref={"step_id": step_id}, state=state, result_ref=result, error=error
        )
        emit_ws_event(db, user_id=user_id, event_type="companion.life.updated", payload={"step_id": step_id})
        await db.commit()
