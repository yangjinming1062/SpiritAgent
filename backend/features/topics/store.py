"""话题业务公开查询与短事务操作；调用方负责提交。"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal

from infrastructure.ai.token_budget import json_tokens
from infrastructure.request_errors import RequestError
from infrastructure.search import extract_search_terms
from infrastructure.text import ensure_utc, utc_now
from infrastructure.web.social_content import validate_source_url
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account

from .models import CompanionTopic, TopicBatch, TopicFeedback, TopicUsage
from .schemas import (
    TopicBatchSnapshot,
    TopicFeedbackSnapshot,
    TopicFeedbackValue,
    TopicSelection,
    TopicSnapshot,
)


def _validate_scope(scope: MemoryScope) -> None:
    if scope.user_id <= 0 or scope.system_preset_id != "companion":
        raise ValueError("Topics require the companion scope")


def _scope(
    model: type[CompanionTopic] | type[TopicBatch] | type[TopicUsage] | type[TopicFeedback], scope: MemoryScope
) -> tuple[Any, Any]:
    _validate_scope(scope)
    return model.user_id == scope.user_id, model.system_preset_id == scope.system_preset_id


async def load_batch(db: AsyncSession, scope: MemoryScope, activity_date: date) -> TopicBatchSnapshot | None:
    row = await db.scalar(
        select(TopicBatch).where(*_scope(TopicBatch, scope), TopicBatch.activity_date == activity_date)
    )
    return TopicBatchSnapshot.model_validate(row) if row else None


async def begin_batch(
    db: AsyncSession,
    scope: MemoryScope,
    activity_date: date,
    *,
    available_from: datetime,
    expires_at: datetime,
    started_at: datetime,
) -> TopicBatchSnapshot:
    _validate_scope(scope)
    await lock_account(db, scope.user_id)
    existing = await load_batch(db, scope, activity_date)
    if existing:
        return existing
    if available_from >= expires_at:
        raise ValueError("Invalid topic availability interval")
    row = TopicBatch(
        user_id=scope.user_id,
        system_preset_id=scope.system_preset_id,
        activity_date=activity_date,
        started_at=started_at,
        window_start=started_at - timedelta(hours=24),
        window_end=started_at,
        available_from=available_from,
        expires_at=expires_at,
        status="collecting",
        search_requests=0,
        detail_requests=0,
        checkpoint={},
    )
    db.add(row)
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


async def _locked_batch(db: AsyncSession, batch_id: int, scope: MemoryScope) -> TopicBatch:
    row = await db.scalar(
        select(TopicBatch)
        .where(TopicBatch.id == batch_id, *_scope(TopicBatch, scope))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("Topic batch is missing")
    return row


async def save_checkpoint(
    db: AsyncSession, batch_id: int, scope: MemoryScope, checkpoint: dict[str, Any]
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status == "collecting":
        row.checkpoint = checkpoint
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


async def reserve_request(
    db: AsyncSession, batch_id: int, scope: MemoryScope, *, kind: Literal["search", "detail"], limit: int
) -> bool:
    row = await _locked_batch(db, batch_id, scope)
    column = "search_requests" if kind == "search" else "detail_requests"
    if row.status != "collecting" or getattr(row, column) >= limit or utc_now() >= ensure_utc(row.expires_at):
        return False
    setattr(row, column, getattr(row, column) + 1)
    await db.flush()
    return True


async def publish_batch(
    db: AsyncSession, batch_id: int, scope: MemoryScope, selections: Sequence[TopicSelection]
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status != "collecting":
        return TopicBatchSnapshot.model_validate(row)
    events: set[str] = set()
    sources: set[tuple[str, str]] = set()
    for selection in selections:
        source = selection.source
        if (
            source.published_at is None
            or not source.has_evidence
            or not ensure_utc(row.window_start) <= source.published_at < ensure_utc(row.window_end)
        ):
            raise ValueError("Topic lacks dated content evidence in the collection window")
        canonical = validate_source_url(source.platform, source.source_id, source.url)
        identity = source.platform, source.source_id
        normalized_event = selection.event_key.casefold()
        if identity in sources or normalized_event in events:
            raise ValueError("Duplicate topic content or event")
        sources.add(identity)
        events.add(normalized_event)
        existing = await db.scalar(
            select(CompanionTopic.id).where(
                *_scope(CompanionTopic, scope),
                CompanionTopic.platform == source.platform,
                CompanionTopic.source_id == source.source_id,
            )
        )
        if existing is not None:
            continue
        db.add(
            CompanionTopic(
                user_id=scope.user_id,
                system_preset_id=scope.system_preset_id,
                batch_id=row.id,
                **source.model_dump(exclude={"url"}),
                url=canonical,
                **selection.model_dump(exclude={"source"}),
            )
        )
    row.status = "ready"
    row.error = None
    row.checkpoint = {}
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


async def finish_failed_batch(db: AsyncSession, batch_id: int, scope: MemoryScope, error: str) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status == "collecting":
        row.status, row.error = "failed", error[:160]
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


def _used(scope: MemoryScope) -> Any:
    return exists(select(TopicUsage.id).where(*_scope(TopicUsage, scope), TopicUsage.topic_id == CompanionTopic.id))


def _active(now: datetime) -> Any:
    return (TopicBatch.status == "ready") & (TopicBatch.available_from <= now) & (TopicBatch.expires_at > now)


def _snapshot(topic: CompanionTopic, used: bool, feedback: str | None) -> TopicSnapshot:
    payload = {
        field: getattr(topic, field)
        for field in TopicSnapshot.model_fields
        if field not in {"type", "topic_id", "is_used", "feedback"}
    }
    payload["url"] = validate_source_url(topic.platform, topic.source_id, topic.url)
    return TopicSnapshot(topic_id=topic.id, is_used=used, feedback=feedback, **payload)


async def _load_topic_snapshots(db: AsyncSession, scope: MemoryScope, *where: Any) -> list[TopicSnapshot]:
    rows = (
        await db.execute(
            select(CompanionTopic, _used(scope), TopicFeedback.value)
            .join(TopicBatch, TopicBatch.id == CompanionTopic.batch_id)
            .outerjoin(TopicFeedback, and_(TopicFeedback.topic_id == CompanionTopic.id, *_scope(TopicFeedback, scope)))
            .where(*_scope(CompanionTopic, scope), *_scope(TopicBatch, scope), *where)
            .order_by(CompanionTopic.id)
        )
    ).all()
    return [_snapshot(topic, was_used, feedback) for topic, was_used, feedback in rows]


async def ranked_topic_summaries(
    db: AsyncSession,
    scope: MemoryScope,
    now: datetime,
    *,
    query: str,
    token_budget: int,
    historical_topic_ids: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """当前批次可供探索；已过采集日的来源按主题匹配，聊天已分享项另受会话水位约束。"""
    if token_budget <= 0:
        return []
    terms = extract_search_terms(query)
    searchable = func.concat_ws(" ", CompanionTopic.title, CompanionTopic.summary, CompanionTopic.event_key)
    interests = func.concat_ws(
        " ", CompanionTopic.user_relevance, CompanionTopic.companion_interest, CompanionTopic.conversation_angle
    )

    def term_score(column: Any) -> Any:
        # 西文使用 PostgreSQL 词干与停用词处理，避免 i、a 等短词命中几乎所有旧闻。
        english = func.to_tsvector("english", column)
        matches = [
            english.op("@@")(func.plainto_tsquery("english", term))
            if term.isascii()
            else column.icontains(term, autoescape=True)
            for term in terms
        ]
        return sum((case((match, 1), else_=0) for match in matches), literal(0))

    score, interest_score = term_score(searchable), term_score(interests)
    used = _used(scope)
    eligibility = or_(TopicBatch.expires_at > now, score > 0)
    if historical_topic_ids is not None:
        eligibility = or_(~used & eligibility, used & CompanionTopic.id.in_(historical_topic_ids))
    fields = (
        CompanionTopic.id.label("topic_id"),
        CompanionTopic.title,
        CompanionTopic.platform,
        CompanionTopic.url,
        CompanionTopic.author,
        CompanionTopic.published_at,
        CompanionTopic.collected_at,
        CompanionTopic.summary,
        CompanionTopic.conversation_angle,
        CompanionTopic.user_relevance,
        CompanionTopic.companion_interest,
        used.label("already_used"),
        (TopicBatch.expires_at <= now).label("past_batch"),
        TopicFeedback.value.label("feedback"),
    )
    stmt = (
        select(*fields)
        .join(TopicBatch, TopicBatch.id == CompanionTopic.batch_id)
        .outerjoin(TopicFeedback, and_(TopicFeedback.topic_id == CompanionTopic.id, *_scope(TopicFeedback, scope)))
        .where(
            *_scope(CompanionTopic, scope),
            *_scope(TopicBatch, scope),
            TopicBatch.status == "ready",
            TopicBatch.available_from <= now,
            eligibility,
        )
        .order_by(
            score.desc(),
            case((TopicFeedback.value == "less", 1), else_=0),
            interest_score.desc(),
            case((TopicFeedback.value == "like", 0), else_=1),
            CompanionTopic.published_at.desc(),
            CompanionTopic.id.desc(),
        )
        .execution_options(yield_per=32)
    )
    summaries = []
    rows = await db.stream(stmt)
    try:
        async for row in rows.mappings():
            brief = {
                **row,
                "topic_id": str(row["topic_id"]),
                "published_at": ensure_utc(row["published_at"]).isoformat(),
                "collected_at": ensure_utc(row["collected_at"]).isoformat(),
            }
            size = json_tokens(brief) + 2
            if size > token_budget:
                if summaries:
                    break
                continue
            summaries.append(brief)
            token_budget -= size
    finally:
        await rows.close()
    return summaries


async def read_saved_topics(db: AsyncSession, scope: MemoryScope, topic_ids: Sequence[int]) -> list[TopicSnapshot]:
    """阅读已有来源不受主动分享时窗限制，也不新增分享记录。"""
    if not topic_ids:
        return []
    return await _load_topic_snapshots(
        db,
        scope,
        CompanionTopic.id.in_(topic_ids),
        TopicBatch.status == "ready",
        TopicBatch.available_from <= utc_now(),
    )


async def has_unshared_topics(db: AsyncSession, scope: MemoryScope, now: datetime) -> bool:
    return (
        await db.scalar(
            select(CompanionTopic.id)
            .join(TopicBatch)
            .where(*_scope(CompanionTopic, scope), *_scope(TopicBatch, scope), _active(now), ~_used(scope))
            .limit(1)
        )
        is not None
    )


async def batch_topic_count(db: AsyncSession, scope: MemoryScope, batch_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(CompanionTopic)
            .where(*_scope(CompanionTopic, scope), CompanionTopic.batch_id == batch_id)
        )
        or 0
    )


async def record_topic_usage(
    db: AsyncSession,
    scope: MemoryScope,
    topic_ids: Sequence[int],
    message_id: int,
    *,
    authorized_at: datetime,
) -> None:
    if not topic_ids:
        return
    if authorized_at is None:
        raise ValueError("Topic authorization time is required")
    if authorized_at.tzinfo is None or authorized_at > utc_now():
        raise ValueError("Invalid topic authorization time")
    topics = list(
        await db.scalars(
            select(CompanionTopic)
            .join(TopicBatch)
            .where(
                *_scope(CompanionTopic, scope),
                CompanionTopic.id.in_(topic_ids),
                *_scope(TopicBatch, scope),
                TopicBatch.status == "ready",
                TopicBatch.available_from <= authorized_at,
            )
        )
    )
    available = {topic.id for topic in topics}
    if any(type(topic_id) is not int or topic_id not in available for topic_id in topic_ids):
        raise ValueError("Topic was not authorized for this reply")
    for topic in topics:
        validate_source_url(topic.platform, topic.source_id, topic.url)
    ids = list(dict.fromkeys(topic_ids))
    existing = set(
        await db.scalars(
            select(TopicUsage.topic_id).where(
                *_scope(TopicUsage, scope), TopicUsage.topic_id.in_(ids), TopicUsage.message_id == message_id
            )
        )
    )
    db.add_all(
        TopicUsage(
            user_id=scope.user_id, system_preset_id=scope.system_preset_id, topic_id=topic_id, message_id=message_id
        )
        for topic_id in ids
        if topic_id not in existing
    )
    await db.flush()


async def recent_topic_feedback(
    db: AsyncSession, scope: MemoryScope, since: datetime, *, limit: int | None = 40
) -> list[dict[str, Any]]:
    usages = (
        select(TopicUsage.topic_id, func.max(TopicUsage.created_at).label("used_at"))
        .where(*_scope(TopicUsage, scope))
        .group_by(TopicUsage.topic_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            CompanionTopic.platform,
            CompanionTopic.title,
            CompanionTopic.published_at,
            CompanionTopic.summary,
            CompanionTopic.user_relevance,
            CompanionTopic.companion_interest,
            CompanionTopic.event_key,
            usages.c.used_at,
            TopicFeedback.value.label("feedback"),
            TopicFeedback.updated_at.label("feedback_updated_at"),
        )
        .outerjoin(usages, usages.c.topic_id == CompanionTopic.id)
        .outerjoin(TopicFeedback, and_(TopicFeedback.topic_id == CompanionTopic.id, *_scope(TopicFeedback, scope)))
        .where(
            *_scope(CompanionTopic, scope),
            or_(usages.c.used_at >= since, TopicFeedback.updated_at >= since, CompanionTopic.created_at >= since),
        )
        .order_by(func.coalesce(TopicFeedback.updated_at, usages.c.used_at, CompanionTopic.created_at).desc())
        .limit(limit)
    )
    return [
        {key: ensure_utc(value).isoformat() if isinstance(value, datetime) else value for key, value in row.items()}
        for row in rows.mappings()
    ]


async def _feedback_topic(db: AsyncSession, scope: MemoryScope, topic_id: int) -> CompanionTopic:
    topic = await db.scalar(
        select(CompanionTopic).where(CompanionTopic.id == topic_id, *_scope(CompanionTopic, scope), _used(scope))
    )
    if topic is None:
        raise RequestError(status_code=404, message="话题不存在或尚未分享")
    return topic


async def get_topic_feedback(
    db: AsyncSession, scope: MemoryScope, topic_ids: Sequence[int]
) -> list[TopicFeedbackSnapshot]:
    ids = list(dict.fromkeys(topic_ids))
    if not ids:
        return []
    rows = await db.execute(
        select(CompanionTopic.id, TopicFeedback.value, TopicFeedback.updated_at)
        .outerjoin(TopicFeedback, and_(TopicFeedback.topic_id == CompanionTopic.id, *_scope(TopicFeedback, scope)))
        .where(*_scope(CompanionTopic, scope), CompanionTopic.id.in_(ids), _used(scope))
    )
    snapshots = {
        topic_id: TopicFeedbackSnapshot(topic_id=topic_id, value=value, updated_at=updated_at)
        for topic_id, value, updated_at in rows
    }
    if any(topic_id not in snapshots for topic_id in ids):
        raise RequestError(status_code=404, message="话题不存在或尚未分享")
    return [snapshots[topic_id] for topic_id in ids]


async def set_topic_feedback(
    db: AsyncSession, scope: MemoryScope, topic_id: int, value: TopicFeedbackValue | None
) -> TopicFeedbackSnapshot:
    if value not in {"like", "less", None}:
        raise ValueError("Invalid topic feedback value")
    await lock_account(db, scope.user_id)
    await _feedback_topic(db, scope, topic_id)
    row = await db.scalar(
        select(TopicFeedback).where(*_scope(TopicFeedback, scope), TopicFeedback.topic_id == topic_id)
    )
    if row is None:
        row = TopicFeedback(
            user_id=scope.user_id, system_preset_id=scope.system_preset_id, topic_id=topic_id, value=value
        )
        db.add(row)
    elif row.value == value:
        return TopicFeedbackSnapshot(topic_id=topic_id, value=row.value, updated_at=row.updated_at)
    else:
        row.value = value
    row.updated_at = utc_now()
    await db.flush()
    snapshot = TopicFeedbackSnapshot(topic_id=topic_id, value=row.value, updated_at=row.updated_at)
    emit_ws_event(
        db,
        user_id=scope.user_id,
        event_type="companion.topic_feedback.updated",
        payload=snapshot.model_dump(mode="json"),
    )
    return snapshot
