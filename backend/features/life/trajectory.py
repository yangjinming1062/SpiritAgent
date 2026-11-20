"""按同一事件身份读取生活轨迹、主题和阅读成果。"""

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from infrastructure.pagination import decode_timestamp_cursor, encode_timestamp_cursor
from sqlalchemy import ColumnElement, and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from features.configuration.settings.timezone import resolve_user_timezone

from .models import LifeEvent, LifeStep
from .queries import event_view, result_reference, step_view
from .schemas import LifeEventView, LifeTrajectoryEntry, LifeTrajectoryListResponse

_PUBLIC_RESULTS = frozenset({"visual_id", "visual_version_id", "outfit_id", "scene_id", "action_id"})


def public_result_reference(row: LifeEvent) -> dict:
    return {key: value for key, value in result_reference(row).items() if key in _PUBLIC_RESULTS}


def trajectory_scope(
    *, pursuit_id: int | None = None, topic_id: int | None = None, shared_only: bool = False
) -> ColumnElement[bool]:
    """每个事项保留自己的成果事件，跨事项浏览时每份成果只出现一次。"""
    scope = LifeEvent.trajectory_kind.is_not(None)
    if shared_only:
        scope = and_(scope, LifeEvent.shared_with_user.is_(True))
    if topic_id is not None:
        scope = and_(scope, func.coalesce(LifeEvent.topic_id, LifeEvent.id) == topic_id)
    if pursuit_id is not None:
        return and_(scope, LifeEvent.pursuit_id == pursuit_id)
    earlier = aliased(LifeEvent)
    duplicate = select(earlier.id).where(
        earlier.user_id == LifeEvent.user_id,
        earlier.trajectory_result_key == LifeEvent.trajectory_result_key,
        earlier.id < LifeEvent.id,
    )
    if shared_only:
        duplicate = duplicate.where(earlier.shared_with_user.is_(True))
    if topic_id is not None:
        duplicate = duplicate.where(func.coalesce(earlier.topic_id, earlier.id) == topic_id)
    return and_(scope, or_(LifeEvent.trajectory_result_key.is_(None), ~duplicate.correlate(LifeEvent).exists()))


async def trajectory_entries(db: AsyncSession, user_id: int, events: Sequence[LifeEvent]) -> list[LifeTrajectoryEntry]:
    if not events:
        return []
    topic = func.coalesce(LifeEvent.topic_id, LifeEvent.id)
    # 主题页合并重复成果，其余公开经历各算一条。搜索日期和当前分页不限制主题范围。
    size = func.count(func.distinct(LifeEvent.trajectory_result_key)) + func.count().filter(
        LifeEvent.trajectory_result_key.is_(None)
    )
    topics = (
        select(topic.label("id"), (size > 1).label("has_related"))
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.shared_with_user.is_(True),
            LifeEvent.trajectory_kind.is_not(None),
            topic.in_({row.topic_id or row.id for row in events}),
        )
        .group_by(topic)
        .subquery()
    )
    topic_info = {
        topic_id: (title, related)
        for topic_id, title, related in await db.execute(
            select(topics.c.id, LifeEvent.fact_json["title"].as_string(), topics.c.has_related)
            .select_from(topics)
            .outerjoin(
                LifeEvent,
                and_(LifeEvent.id == topics.c.id, LifeEvent.user_id == user_id, LifeEvent.shared_with_user.is_(True)),
            )
        )
    }
    steps = {row.step_id for row in events if row.step_id is not None}
    readings = (
        {
            row.id: step_view(row).reading_result
            for row in await db.scalars(
                select(LifeStep).where(
                    LifeStep.user_id == user_id, LifeStep.id.in_(steps), LifeStep.kind == "content.read"
                )
            )
        }
        if steps
        else {}
    )
    zone = ZoneInfo(await resolve_user_timezone(db, user_id) or "UTC")
    entries = []
    for row in events:
        if row.trajectory_kind is None:
            continue
        topic_id = row.topic_id or row.id
        title, related = topic_info.get(topic_id, (None, False))
        entries.append(
            LifeTrajectoryEntry(
                id=row.id,
                kind=row.trajectory_kind,
                fact_type=row.fact_type,
                title=row.fact_json.get("title"),
                topic_title=title or row.fact_json.get("title"),
                has_related_episodes=related,
                narrative=row.narrative,
                occurred_at=row.occurred_at,
                local_date=row.occurred_at.astimezone(zone).date(),
                topic_id=topic_id,
                previous_episode_id=row.previous_episode_id,
                pursuit_id=row.pursuit_id,
                reading_result=readings.get(row.step_id),
                result_ref=public_result_reference(row),
            )
        )
    return entries


async def list_trajectory(
    db: AsyncSession,
    user_id: int,
    *,
    pursuit_id: int | None = None,
    topic_id: int | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> LifeTrajectoryListResponse:
    where = [
        LifeEvent.user_id == user_id,
        trajectory_scope(pursuit_id=pursuit_id, topic_id=topic_id, shared_only=True),
    ]
    if position := decode_timestamp_cursor(cursor):
        where.append(tuple_(LifeEvent.occurred_at, LifeEvent.id) < position)
    rows = list(
        await db.scalars(
            select(LifeEvent).where(*where).order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc()).limit(limit + 1)
        )
    )
    visible = rows[:limit]
    more = len(rows) > limit
    return LifeTrajectoryListResponse(
        items=await trajectory_entries(db, user_id, visible),
        has_more=more,
        next_cursor=encode_timestamp_cursor(visible[-1].occurred_at, visible[-1].id) if more else None,
    )


async def trajectory_topic_heads(db: AsyncSession, user_id: int, *, limit: int = 6) -> list[LifeEventView]:
    topic = func.coalesce(LifeEvent.topic_id, LifeEvent.id)
    heads = (
        select(LifeEvent.id)
        .where(
            LifeEvent.user_id == user_id,
            LifeEvent.trajectory_kind.is_not(None),
            LifeEvent.source != "life_share",
        )
        .distinct(topic)
        .order_by(topic, LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
    )
    events = await db.scalars(
        select(LifeEvent)
        .where(LifeEvent.user_id == user_id, LifeEvent.id.in_(heads))
        .order_by(LifeEvent.occurred_at.desc(), LifeEvent.id.desc())
        .limit(limit)
    )
    return [event_view(row) for row in events]
