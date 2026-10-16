"""话题业务公开查询与短事务操作；调用方负责提交。"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal

from infrastructure.request_errors import RequestError
from infrastructure.text import ensure_utc, utc_now
from infrastructure.web.social_content import validate_source_url
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account
from features.configuration.settings.values import get_user_setting

from .models import CompanionTopic, TopicBatch, TopicFeedback, TopicUsage
from .schemas import (
    TopicBatchSnapshot,
    TopicCardSnapshot,
    TopicFeedbackSnapshot,
    TopicFeedbackValue,
    TopicSelection,
    TopicSnapshot,
)


def _validate_scope(scope: MemoryScope) -> None:
    if scope.user_id <= 0 or scope.system_preset_id != "companion":
        raise ValueError("Topics require the companion scope")


def _scope(
    model: type[CompanionTopic] | type[TopicBatch] | type[TopicUsage] | type[TopicFeedback],
    scope: MemoryScope,
) -> tuple[Any, Any]:
    _validate_scope(scope)
    return model.user_id == scope.user_id, model.system_preset_id == scope.system_preset_id


async def load_batch(db: AsyncSession, scope: MemoryScope, activity_date: date) -> TopicBatchSnapshot | None:
    row = await db.scalar(
        select(TopicBatch).where(*_scope(TopicBatch, scope), TopicBatch.activity_date == activity_date),
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
        .execution_options(populate_existing=True),
    )
    if row is None:
        raise ValueError("Topic batch is missing")
    return row


async def save_checkpoint(
    db: AsyncSession,
    batch_id: int,
    scope: MemoryScope,
    checkpoint: dict[str, Any],
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status == "collecting":
        row.checkpoint = checkpoint
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


async def reserve_request(
    db: AsyncSession,
    batch_id: int,
    scope: MemoryScope,
    *,
    kind: Literal["search", "detail"],
    limit: int,
) -> bool:
    row = await _locked_batch(db, batch_id, scope)
    if kind not in {"search", "detail"}:
        raise ValueError("Invalid topic request kind")
    column = "search_requests" if kind == "search" else "detail_requests"
    if row.status != "collecting" or getattr(row, column) >= limit or utc_now() >= ensure_utc(row.expires_at):
        return False
    setattr(row, column, getattr(row, column) + 1)
    await db.flush()
    return True


async def publish_batch(
    db: AsyncSession,
    batch_id: int,
    scope: MemoryScope,
    selections: Sequence[TopicSelection],
    *,
    limit: int = 5,
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status != "collecting":
        return TopicBatchSnapshot.model_validate(row)
    if len(selections) > min(5, limit):
        raise ValueError("Topic reserve exceeds limit")
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
            ),
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
            ),
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


async def load_prompt_topics(
    db: AsyncSession,
    scope: MemoryScope,
    now: datetime,
    *,
    historical_topic_ids: Sequence[int] = (),
) -> list[TopicSnapshot]:
    used = _used(scope)
    enabled = await get_user_setting(db, scope.user_id, "companion.information_discovery_enabled") is True
    query = (
        select(CompanionTopic, used, TopicFeedback.value)
        .join(TopicBatch, TopicBatch.id == CompanionTopic.batch_id)
        .outerjoin(TopicFeedback, TopicFeedback.topic_id == CompanionTopic.id)
        .where(
            *_scope(CompanionTopic, scope),
            or_(_active(now) if enabled else False, CompanionTopic.id.in_(historical_topic_ids) & used),
        )
        .order_by(CompanionTopic.id)
    )
    return [_snapshot(topic, was_used, feedback) for topic, was_used, feedback in (await db.execute(query)).all()]


async def has_unshared_topics(db: AsyncSession, scope: MemoryScope, now: datetime) -> bool:
    if await get_user_setting(db, scope.user_id, "companion.information_discovery_enabled") is not True:
        return False
    return (
        await db.scalar(
            select(CompanionTopic.id)
            .join(TopicBatch)
            .where(*_scope(CompanionTopic, scope), _active(now), ~_used(scope))
            .limit(1),
        )
        is not None
    )


async def load_batch_topics(db: AsyncSession, scope: MemoryScope, batch_id: int) -> list[TopicSnapshot]:
    rows = (
        await db.execute(
            select(CompanionTopic, _used(scope), TopicFeedback.value)
            .join(TopicBatch, TopicBatch.id == CompanionTopic.batch_id)
            .outerjoin(TopicFeedback, TopicFeedback.topic_id == CompanionTopic.id)
            .where(*_scope(CompanionTopic, scope), TopicBatch.id == batch_id, TopicBatch.status == "ready")
            .order_by(CompanionTopic.id),
        )
    ).all()
    return [_snapshot(topic, was_used, feedback) for topic, was_used, feedback in rows]


async def resolve_topic_cards(
    db: AsyncSession,
    scope: MemoryScope,
    topic_ids: Sequence[int],
    now: datetime,
) -> list[TopicCardSnapshot]:
    snapshots = await load_prompt_topics(db, scope, now, historical_topic_ids=topic_ids)
    by_id = {topic.topic_id: topic for topic in snapshots}
    if any(type(topic_id) is not int or topic_id not in by_id for topic_id in topic_ids):
        raise ValueError("Topic reference is not available in this companion scope")
    return [
        TopicCardSnapshot.model_validate(by_id[topic_id].model_dump(include=set(TopicCardSnapshot.model_fields)))
        for topic_id in dict.fromkeys(topic_ids)
    ]


async def record_topic_usage(
    db: AsyncSession,
    scope: MemoryScope,
    topic_ids: Sequence[int],
    message_id: int,
    *,
    authorized_at: datetime | None = None,
) -> None:
    if not topic_ids:
        return
    if authorized_at is None:
        ids = [card.topic_id for card in await resolve_topic_cards(db, scope, topic_ids, utc_now())]
    else:
        if authorized_at.tzinfo is None or authorized_at > utc_now():
            raise ValueError("Invalid topic authorization time")
        topics = list(
            await db.scalars(
                select(CompanionTopic)
                .join(TopicBatch)
                .where(
                    *_scope(CompanionTopic, scope),
                    CompanionTopic.id.in_(topic_ids),
                    or_(_active(authorized_at), _used(scope)),
                ),
            ),
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
                *_scope(TopicUsage, scope),
                TopicUsage.topic_id.in_(ids),
                TopicUsage.message_id == message_id,
            ),
        ),
    )
    db.add_all(
        TopicUsage(
            user_id=scope.user_id,
            system_preset_id=scope.system_preset_id,
            topic_id=topic_id,
            message_id=message_id,
        )
        for topic_id in ids
        if topic_id not in existing
    )
    await db.flush()


async def validate_restored_topic_cards(
    db: AsyncSession,
    scope: MemoryScope,
    cards: Sequence[TopicCardSnapshot],
) -> None:
    """恢复使用证据前验证来源绑定；恢复资料不能扩大来源归属或更换原文地址。"""
    topics = list(
        await db.scalars(
            select(CompanionTopic).where(
                *_scope(CompanionTopic, scope),
                CompanionTopic.id.in_([card.topic_id for card in cards]),
            ),
        ),
    )
    by_id = {topic.id: topic for topic in topics}
    for card in cards:
        topic = by_id.get(card.topic_id)
        if (
            topic is None
            or card.platform != topic.platform
            or card.title != topic.title
            or ensure_utc(card.published_at) != ensure_utc(topic.published_at)
            or card.url != validate_source_url(topic.platform, topic.source_id, topic.url)
        ):
            raise ValueError("Restored link does not match its topic source")


async def recent_topic_feedback(
    db: AsyncSession,
    scope: MemoryScope,
    since: datetime,
    *,
    limit: int | None = 40,
) -> list[dict[str, Any]]:
    usages = (
        select(TopicUsage.topic_id, func.max(TopicUsage.created_at).label("used_at"))
        .where(*_scope(TopicUsage, scope))
        .group_by(TopicUsage.topic_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(CompanionTopic, usages.c.used_at, TopicFeedback.value, TopicFeedback.updated_at)
            .outerjoin(usages, usages.c.topic_id == CompanionTopic.id)
            .outerjoin(TopicFeedback, TopicFeedback.topic_id == CompanionTopic.id)
            .where(
                *_scope(CompanionTopic, scope),
                or_(usages.c.used_at >= since, TopicFeedback.updated_at >= since, CompanionTopic.created_at >= since),
            )
            .order_by(func.coalesce(TopicFeedback.updated_at, usages.c.used_at, CompanionTopic.created_at).desc())
            .limit(limit),
        )
    ).all()
    return [
        {
            **_snapshot(topic, used_at is not None, feedback).model_dump(mode="json"),
            "used_at": ensure_utc(used_at).isoformat() if used_at else None,
            "feedback_updated_at": ensure_utc(feedback_at).isoformat() if feedback_at else None,
        }
        for topic, used_at, feedback, feedback_at in rows
    ]


async def _feedback_topic(db: AsyncSession, scope: MemoryScope, topic_id: int) -> CompanionTopic:
    topic = await db.scalar(
        select(CompanionTopic).where(CompanionTopic.id == topic_id, *_scope(CompanionTopic, scope), _used(scope)),
    )
    if topic is None:
        raise RequestError(status_code=404, message="话题不存在或尚未分享")
    return topic


async def get_topic_feedback(
    db: AsyncSession,
    scope: MemoryScope,
    topic_ids: Sequence[int],
) -> list[TopicFeedbackSnapshot]:
    ids = list(dict.fromkeys(topic_ids))
    if not ids:
        return []
    rows = await db.execute(
        select(CompanionTopic.id, TopicFeedback.value, TopicFeedback.updated_at)
        .outerjoin(TopicFeedback, and_(TopicFeedback.topic_id == CompanionTopic.id, *_scope(TopicFeedback, scope)))
        .where(*_scope(CompanionTopic, scope), CompanionTopic.id.in_(ids), _used(scope)),
    )
    snapshots = {
        topic_id: TopicFeedbackSnapshot(topic_id=topic_id, value=value, updated_at=updated_at)
        for topic_id, value, updated_at in rows
    }
    if any(topic_id not in snapshots for topic_id in ids):
        raise RequestError(status_code=404, message="话题不存在或尚未分享")
    return [snapshots[topic_id] for topic_id in ids]


async def set_topic_feedback(
    db: AsyncSession,
    scope: MemoryScope,
    topic_id: int,
    value: TopicFeedbackValue | None,
) -> TopicFeedbackSnapshot:
    if value not in {"like", "less", None}:
        raise ValueError("Invalid topic feedback value")
    await lock_account(db, scope.user_id)
    await _feedback_topic(db, scope, topic_id)
    row = await db.scalar(
        select(TopicFeedback).where(*_scope(TopicFeedback, scope), TopicFeedback.topic_id == topic_id),
    )
    if row is None:
        row = TopicFeedback(
            user_id=scope.user_id,
            system_preset_id=scope.system_preset_id,
            topic_id=topic_id,
            value=value,
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
