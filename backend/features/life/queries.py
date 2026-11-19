"""只读查询；调用方不直接读取 Life ORM。"""

from collections.abc import Collection, Sequence
from datetime import datetime

from infrastructure.library_visibility import visible_library_work
from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from infrastructure.text import utc_now
from sqlalchemy import CompoundSelect, case, func, or_, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from .actions import ReadingResult
from .models import RESULT_REFERENCE_FIELDS, LifeEvent, LifePursuit, LifeState, LifeStep, PursuitEvidence
from .prompts import life_content_payload
from .schemas import (
    EVIDENCE_EVENT_KINDS,
    EVIDENCE_EVENTS_PER_PURSUIT,
    PUBLISHED_ENTRY_KINDS,
    UNRESOLVED_STEP_STATES,
    LifeEventPublicListResponse,
    LifeEventPublicView,
    LifeEventView,
    LifePursuitEvidence,
    LifePursuitListResponse,
    LifePursuitView,
    LifeStateView,
    LifeStepView,
    PersonalWorkView,
)


def referenced_visual_versions(user_id: int) -> CompoundSelect[tuple[int | None]]:
    """生活成果引用的是发布当时的版本，回收条件在同一数据库语句中检查。"""
    return union_all(
        select(LifeEvent.result_visual_version_id).where(
            LifeEvent.user_id == user_id, LifeEvent.result_visual_version_id.is_not(None)
        ),
        select(LifeStep.result_visual_version_id).where(
            LifeStep.user_id == user_id, LifeStep.result_visual_version_id.is_not(None)
        ),
    )


def state_view(row: LifeState) -> LifeStateView:
    return LifeStateView(
        user_id=row.user_id,
        current_activity=row.current_activity,
        narrative=row.narrative,
        activity_kind=row.activity_kind,
        activity_type=row.activity_type,
        scene_tags=row.scene_tags_json,
        activity_started_at=row.activity_started_at,
        activity_step_id=row.activity_step_id,
        activity_shared_with_user=row.activity_shared_with_user,
        presentation_revision=row.presentation_revision,
        current_scene=row.current_scene_json,
        mood=row.mood,
        active_pursuit_id=row.active_pursuit_id,
        next_review_at=row.next_review_at,
        revision=row.revision,
        scene_revision=row.scene_revision,
        updated_at=row.updated_at,
    )


async def active_pursuit_title(db: AsyncSession, user_id: int, pursuit_id: int | None) -> str:
    """读取当前生活事项标题，供跨域呈现投影使用；不存在时保持空值。"""

    if pursuit_id is None:
        return ""
    return (
        await db.scalar(
            select(LifePursuit.title).where(
                LifePursuit.user_id == user_id, LifePursuit.id == pursuit_id, LifePursuit.shared_with_user.is_(True)
            )
        )
        or ""
    )


async def completed_personal_works(
    db: AsyncSession, user_id: int, *, event_id: int | None = None, limit: int = 8
) -> list[PersonalWorkView]:
    """直接分享按原事件核对，日常选择只拿仍在自己手中的作品。"""
    query = (
        select(LifeEvent, LifeStep)
        .join(LifeStep, LifeStep.id == LifeEvent.step_id)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.kind == "life.step.completed",
            LifeStep.user_id == user_id,
            LifeStep.state == "completed",
            LifeStep.is_creation.is_(True),
            LifeStep.library_work_id.is_not(None),
            or_(
                LifeEvent.result_visual_id.is_not(None) & LifeEvent.result_visual_version_id.is_not(None),
                LifeEvent.result_outfit_id.is_not(None),
                LifeEvent.result_scene_id.is_not(None),
                LifeEvent.result_action_id.is_not(None),
            ),
        )
    )
    query = (
        query.where(LifeEvent.id == event_id)
        if event_id is not None
        else query.where(~visible_library_work(LifeStep.library_work_id))
    )
    rows = await db.execute(query.order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(limit))
    return [
        PersonalWorkView(
            **event_view(event).model_dump(),
            creation_brief={
                "kind": step.kind,
                "purpose": step.description,
                "requested": life_content_payload(step.action_json or {}),
            },
        )
        for event, step in rows
    ]


async def steps_by_id(db: AsyncSession, user_id: int, identifiers: Collection[int]) -> dict[int, LifeStepView]:
    if not identifiers:
        return {}
    rows = await db.scalars(select(LifeStep).where(LifeStep.user_id == user_id, LifeStep.id.in_(identifiers)))
    return {row.id: step_view(row) for row in rows}


def empty_state_view(user_id: int) -> LifeStateView:
    """返回尚未落库账户的初始生活状态。"""

    return LifeStateView(
        user_id=user_id,
        current_activity="",
        activity_type="idle",
        current_scene={},
        mood=None,
        active_pursuit_id=None,
        next_review_at=None,
        revision=0,
        scene_revision=0,
        updated_at=utc_now(),
    )


def result_reference(row: LifeStep | LifeEvent) -> dict:
    result = dict(row.result_ref_json)
    for key, field in RESULT_REFERENCE_FIELDS.items():
        value = getattr(row, field)
        if value is not None:
            result[key] = value
    return result


async def visible_completion_results(db: AsyncSession, user_id: int, pursuit_ids: Collection[int]) -> dict[int, dict]:
    """结项只展示当次采用且现在可见的原始成果，保留删除和后续公开的语义。"""
    grouped: dict[int, list[dict]] = {key: [] for key in pursuit_ids}
    if grouped:
        records = await db.execute(
            select(PursuitEvidence.pursuit_id, LifeEvent)
            .join(LifeEvent, LifeEvent.id == PursuitEvidence.event_id)
            .outerjoin(LifeStep, LifeStep.id == LifeEvent.step_id)
            .where(
                PursuitEvidence.user_id == user_id,
                LifeEvent.user_id == user_id,
                PursuitEvidence.pursuit_id.in_(pursuit_ids),
                PursuitEvidence.used_for_completion.is_(True),
                or_(LifeEvent.kind != "life.step.completed", LifeStep.user_id == user_id),
                visible_library_work(LifeStep.library_work_id),
            )
            .order_by(LifeEvent.id)
        )
        for pursuit_id, event in records:
            if reference := result_reference(event):
                grouped[pursuit_id].append(reference)
    return {key: {"evidence_count": len(results), "results": results} for key, results in grouped.items()}


async def project_public_completions(
    db: AsyncSession, user_id: int, views: Sequence[LifePursuitView | LifeEventView]
) -> None:
    targets = []
    for item in views:
        if isinstance(item, LifePursuitView) and item.status == "completed":
            targets.append((item, item.id))
        elif isinstance(item, LifeEventView) and item.kind == "life.pursuit.completed":
            targets.append((item, item.pursuit_id))
    visible = await visible_completion_results(db, user_id, {key for _, key in targets if key is not None})
    for item, key in targets:
        item.result_ref = visible[key] if key is not None else {"evidence_count": 0, "results": []}


def pursuit_view(row: LifePursuit) -> LifePursuitView:
    return LifePursuitView(
        id=row.id,
        title=row.title,
        narrative=row.narrative,
        shared_with_user=row.shared_with_user,
        reason=row.reason,
        desired_result=row.desired_result,
        progress=row.progress,
        next_step=row.next_step,
        status=row.status,
        status_reason=row.status_reason,
        status_source=row.status_source,
        status_changed_at=row.status_changed_at,
        resume_after=row.resume_after,
        previous_pursuit_id=row.previous_pursuit_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        result_ref=row.result_ref_json,
        updated_at=row.updated_at,
    )


def step_view(row: LifeStep) -> LifeStepView:
    return LifeStepView(
        library_work_id=row.library_work_id,
        id=row.id,
        pursuit_id=row.pursuit_id,
        idempotency_key=row.idempotency_key,
        kind=row.kind,
        description=row.description,
        narrative=row.narrative,
        state=row.state,
        source=row.source,
        action=row.action_json,
        disposition=row.disposition,
        wait_reason=row.wait_reason,
        not_before=row.not_before,
        reading_result=ReadingResult.model_validate(row.reading_result_json).model_dump(
            exclude={"sources": {"__all__": {"content"}}}
        )
        if row.reading_result_json
        else None,
        business_ref={
            **({"job_id": row.job_id, "generation_id": row.generation_id} if row.job_id is not None else {}),
            **({"action_id": row.business_action_id} if row.business_action_id is not None else {}),
        },
        result_ref=result_reference(row),
        error=row.error,
        failure_code=row.failure_code,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def event_view(row: LifeEvent) -> LifeEventView:
    return LifeEventView(
        id=row.id,
        event_key=row.event_key,
        kind=row.kind,
        source=row.source,
        subject=row.subject,
        fact_type=row.fact_type,
        fact=row.fact_json,
        narrative=row.narrative,
        occurred_at=row.occurred_at,
        pursuit_id=row.pursuit_id,
        step_id=row.step_id,
        result_ref=result_reference(row),
        is_meaningful=row.is_meaningful,
        shared_with_user=row.shared_with_user,
        trajectory_kind=row.trajectory_kind,
        topic_id=(row.topic_id or row.id) if row.trajectory_kind else None,
        previous_episode_id=row.previous_episode_id,
    )


async def get_life_state(db: AsyncSession, user_id: int) -> LifeState | None:
    return await db.scalar(select(LifeState).where(LifeState.user_id == user_id))


async def get_life_mood(db: AsyncSession, user_id: int) -> str | None:
    """读取当前心情，避免调用方依赖 Life ORM。"""
    return await db.scalar(select(LifeState.mood).where(LifeState.user_id == user_id))


async def get_life_state_view(db: AsyncSession, user_id: int) -> LifeStateView | None:
    row = await get_life_state(db, user_id)
    return state_view(row) if row else None


async def get_pursuit(db: AsyncSession, user_id: int, pursuit_id: int) -> LifePursuit | None:
    return await db.scalar(select(LifePursuit).where(LifePursuit.user_id == user_id, LifePursuit.id == pursuit_id))


async def get_pursuit_view(db: AsyncSession, user_id: int, pursuit_id: int) -> LifePursuitView | None:
    row = await get_pursuit(db, user_id, pursuit_id)
    return pursuit_view(row) if row is not None else None


async def due_review_user_ids(db: AsyncSession, user_ids: Collection[int], now: datetime) -> set[int]:
    """缺少生活状态的就绪账户也需要首次判断。"""
    if not user_ids:
        return set()
    waiting = set(
        await db.scalars(
            select(LifeState.user_id).where(LifeState.user_id.in_(user_ids), LifeState.next_review_at > now)
        )
    )
    return set(user_ids) - waiting


async def pursuit_evidence(
    db: AsyncSession, user_id: int, pursuit_ids: Collection[int], *, include_publications: bool = False
) -> LifePursuitEvidence:
    """按事项补充未解决步骤与可引用的结果依据。"""
    if not pursuit_ids:
        return LifePursuitEvidence()
    steps = await db.scalars(
        select(LifeStep)
        .where(
            LifeStep.user_id == user_id,
            LifeStep.pursuit_id.in_(pursuit_ids),
            LifeStep.state.in_(UNRESOLVED_STEP_STATES),
        )
        .order_by(LifeStep.pursuit_id, LifeStep.id)
    )
    scope = LifeEvent.pursuit_id.in_(pursuit_ids)
    if include_publications:
        scope = or_(
            scope,
            LifeEvent.pursuit_id.is_(None)
            & LifeEvent.kind.in_(PUBLISHED_ENTRY_KINDS)
            & LifeEvent.result_entry_id.is_not(None),
        )
    ranked = (
        select(
            LifeEvent.id,
            func.row_number()
            .over(
                partition_by=(LifeEvent.pursuit_id, case((LifeEvent.pursuit_id.is_(None), LifeEvent.kind))),
                order_by=(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()),
            )
            .label("position"),
        )
        .where(
            LifeEvent.user_id == user_id,
            scope,
            LifeEvent.subject == "companion",
            LifeEvent.is_meaningful.is_(True),
            LifeEvent.kind.in_(EVIDENCE_EVENT_KINDS),
            LifeEvent.fact_type == "result",
            or_(
                LifeEvent.result_ref_json != {},
                *(getattr(LifeEvent, field).is_not(None) for field in RESULT_REFERENCE_FIELDS.values()),
            ),
        )
        .subquery()
    )
    events = await db.scalars(
        select(LifeEvent)
        .join(ranked, LifeEvent.id == ranked.c.id)
        .where(ranked.c.position <= EVIDENCE_EVENTS_PER_PURSUIT)
        .order_by(LifeEvent.pursuit_id, LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
    )
    return LifePursuitEvidence(steps=[step_view(row) for row in steps], events=[event_view(row) for row in events])


async def list_pursuits(
    db: AsyncSession,
    user_id: int,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
    shared_only: bool = False,
) -> LifePursuitListResponse:
    where = [LifePursuit.user_id == user_id]
    if shared_only:
        where.append(LifePursuit.shared_with_user.is_(True))
    if status:
        where.append(LifePursuit.status == status)
    if before := decode_timestamp_cursor(cursor):
        where.append(tuple_(LifePursuit.updated_at, LifePursuit.id) < before)
    rows = list(
        await db.scalars(
            select(LifePursuit)
            .where(*where)
            .order_by(LifePursuit.updated_at.desc(), LifePursuit.id.desc())
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [pursuit_view(row) for row in rows]
    if shared_only:
        await project_public_completions(db, user_id, items)
        visible_previous = await shared_pursuit_ids(
            db, user_id, {item.previous_pursuit_id for item in items if item.previous_pursuit_id is not None}
        )
        for item in items:
            if item.previous_pursuit_id not in visible_previous:
                item.previous_pursuit_id = None
    return LifePursuitListResponse(
        items=items,
        has_more=more,
        next_cursor=encode_timestamp_cursor(rows[-1].updated_at, rows[-1].id) if more else None,
    )


async def list_public_events(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_id: int | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> LifeEventPublicListResponse:
    where = [LifeEvent.user_id == user_id, LifeEvent.shared_with_user.is_(True)]
    if pursuit_id is not None:
        where.append(LifeEvent.pursuit_id == pursuit_id)
    if position := decode_timestamp_cursor(cursor):
        where.append(tuple_(LifeEvent.occurred_at, LifeEvent.id) < position)
    rows = list(
        await db.scalars(
            select(LifeEvent).where(*where).order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    items = [event_view(row) for row in rows]
    await project_public_completions(db, user_id, items)
    return LifeEventPublicListResponse(
        items=[LifeEventPublicView.model_validate(item) for item in items],
        has_more=more,
        next_cursor=encode_timestamp_cursor(rows[-1].occurred_at, rows[-1].id) if more else None,
    )


async def shared_pursuit_ids(db: AsyncSession, user_id: int, ids: Collection[int]) -> set[int]:
    if not ids:
        return set()
    return set(
        await db.scalars(
            select(LifePursuit.id).where(
                LifePursuit.user_id == user_id, LifePursuit.id.in_(ids), LifePursuit.shared_with_user.is_(True)
            )
        )
    )
