"""持续事项的暂停、恢复和正常退出，保留已经提交的执行事实。"""

from infrastructure.text import ensure_utc, utc_now
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LifeActionAdmission, LifePursuit, LifeStep
from .schemas import LifeDecisionRequest, LifeEventInput
from .service import LifeInputError, _latest_active_pursuit_id, clear_activity_step, get_or_create_state, record_events


async def transition_pursuit(db: AsyncSession, user_id: int, decision: LifeDecisionRequest) -> LifePursuit:
    state = await get_or_create_state(db, user_id, lock=True)
    pursuit = await db.scalar(
        select(LifePursuit)
        .where(LifePursuit.user_id == user_id, LifePursuit.id == decision.pursuit_id)
        .with_for_update()
    )
    if pursuit is None:
        raise LifeInputError("持续事项不存在")
    if not decision.status_reason.strip():
        raise LifeInputError("调整事项必须说明原因")
    target = {
        "pause_pursuit": "paused",
        "resume_pursuit": "active",
        "abandon_pursuit": "abandoned",
        "redirect_pursuit": "abandoned",
    }[decision.operation]
    if pursuit.status in {"completed", "abandoned"}:
        raise LifeInputError("已结束的事项不能改写，请开始新的事项")
    if (
        target == "paused"
        and pursuit.status == "paused"
        and pursuit.status_source == "user"
        and decision.source != "user"
    ):
        raise LifeInputError("不能覆盖用户暂停事项的恢复要求")
    if target == "active":
        if pursuit.status != "paused":
            raise LifeInputError("只有暂停的事项可以恢复")
        if pursuit.status_source == "user" and decision.source != "user":
            raise LifeInputError("用户暂停的事项须由用户要求恢复")
        if decision.source != "user" and pursuit.resume_after and ensure_utc(pursuit.resume_after) > utc_now():
            raise LifeInputError("尚未到事项恢复时间")
    pursuit.status = target
    pursuit.status_reason = decision.status_reason.strip()
    pursuit.narrative = (
        decision.narrative.event if decision.operation == "redirect_pursuit" else decision.narrative.pursuit
    )
    pursuit.status_source = decision.source
    pursuit.status_changed_at = utc_now()
    pursuit.resume_after = decision.resume_after if target == "paused" else None
    if target == "active":
        state.active_pursuit_id = pursuit.id
        state.next_review_at = utc_now()
    elif state.active_pursuit_id == pursuit.id:
        state.active_pursuit_id = await _latest_active_pursuit_id(db, user_id, exclude=pursuit.id)
    events = []
    if target in {"paused", "abandoned"}:
        if target == "abandoned":
            pursuit.next_step = ""
        steps = await db.execute(
            select(LifeStep, LifeActionAdmission.submitted)
            .outerjoin(
                LifeActionAdmission,
                and_(
                    LifeActionAdmission.user_id == LifeStep.user_id,
                    LifeActionAdmission.request_id == LifeStep.idempotency_key,
                ),
            )
            .where(
                LifeStep.user_id == user_id,
                LifeStep.pursuit_id == pursuit.id,
                or_(
                    LifeStep.state == "planned",
                    (LifeStep.state == "running") & LifeStep.job_id.is_(None) & LifeStep.business_action_id.is_(None),
                ),
            )
        )
        for step, submitted in steps:
            if step.state == "running" and submitted:
                continue
            if state.activity_step_id == step.id:
                clear_activity_step(db, state)
            if target == "paused":
                step.state, step.disposition, step.wait_reason = "planned", "waiting", decision.status_reason
                continue
            step.state, step.finished_at = "cancelled", utc_now()
            step.disposition = "cancelled"
            step.wait_reason = decision.status_reason
            events.append(
                LifeEventInput(
                    event_key=f"step:{step.id}:cancelled",
                    kind="life.step.cancelled",
                    source="life_decision",
                    fact_type="execution",
                    pursuit_id=pursuit.id,
                    step_id=step.id,
                    fact={"description": step.description, "reason": decision.status_reason},
                ),
            )
    events.append(
        LifeEventInput(
            event_key=f"pursuit:{pursuit.id}:{state.revision}:{target}",
            kind=f"life.pursuit.{target}",
            source="life_decision",
            fact_type="intent",
            pursuit_id=pursuit.id,
            fact={"title": pursuit.title, "reason": pursuit.status_reason, "source": decision.source},
            narrative=decision.narrative.event or decision.narrative.pursuit,
        ),
    )
    await record_events(db, user_id, events)
    return pursuit
