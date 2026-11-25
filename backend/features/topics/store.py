"""话题业务公开查询与短事务操作；调用方负责提交。"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from itertools import zip_longest
from typing import Any, Literal

from infrastructure.ai.decision import (
    DecisionOption,
    DecisionQuestion,
    choose_decisions,
    decision_enabled,
    decision_input_fits,
)
from infrastructure.ai.token_budget import json_tokens
from infrastructure.database import SESSION_LOCAL
from infrastructure.request_errors import RequestError
from infrastructure.search import extract_search_terms
from infrastructure.text import ensure_utc, utc_now
from infrastructure.web.social_content import SocialContent, validate_source_url
from runtime.events.emit import emit_ws_event
from runtime.memory_scope import MemoryScope
from sqlalchemy import Select, and_, case, delete, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from features.accounts.locking import lock_account

from .models import CompanionTopic, TopicBatch, TopicCandidate, TopicFeedback, TopicUsage
from .schemas import (
    TopicBatchSnapshot,
    TopicCandidateChoice,
    TopicFeedbackSnapshot,
    TopicFeedbackValue,
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


async def count_request(db: AsyncSession, batch_id: int, scope: MemoryScope, kind: Literal["search", "detail"]) -> bool:
    row = await _locked_batch(db, batch_id, scope)
    if row.status != "collecting" or utc_now() >= ensure_utc(row.expires_at):
        return False
    if kind == "search":
        row.search_requests += 1
    else:
        row.detail_requests += 1
    return True


async def collecting_batches(db: AsyncSession) -> list[tuple[int, date]]:
    rows = await db.execute(
        select(TopicBatch.user_id, TopicBatch.activity_date).where(TopicBatch.status == "collecting")
    )
    return [(user_id, activity_date) for user_id, activity_date in rows]


def candidate_key(source: SocialContent) -> str:
    return f"{source.platform}:{source.source_id}"


async def stage_candidates(
    db: AsyncSession, scope: MemoryScope, batch_id: int, sources: Sequence[SocialContent], *, read_details: bool
) -> tuple[int, int]:
    row = await _locked_batch(db, batch_id, scope)
    if not sources or row.status != "collecting":
        return 0, 0
    values = []
    for item in sources:
        outside = item.published_at is not None and not row.window_start <= item.published_at < row.window_end
        values.append(
            {
                "batch_id": batch_id,
                "candidate_key": candidate_key(item),
                "content": {} if outside else item.model_dump(mode="json"),
                "detailed": outside or not read_details or not item.needs_detail,
                "reviewed": outside,
            }
        )
    result = await db.scalars(
        insert(TopicCandidate)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_topic_candidate_source")
        .returning(TopicCandidate.reviewed)
    )
    added = result.all()
    return len(added), sum(added)


def _candidates(batch_id: int, scope: MemoryScope) -> Select[tuple[TopicCandidate]]:
    return (
        select(TopicCandidate)
        .join(TopicBatch)
        .where(*_scope(TopicBatch, scope), TopicBatch.id == batch_id, TopicBatch.status == "collecting")
    )


async def candidate_page(
    db: AsyncSession, scope: MemoryScope, batch_id: int, *, for_review: bool
) -> list[SocialContent]:
    rows = await db.scalars(
        _candidates(batch_id, scope)
        .where(~TopicCandidate.reviewed, TopicCandidate.detailed == for_review)
        .order_by(TopicCandidate.id)
        .limit(64)
    )
    return [SocialContent.model_validate(row.content) for row in rows]


async def save_details(db: AsyncSession, scope: MemoryScope, batch_id: int, source: SocialContent) -> None:
    batch = await _locked_batch(db, batch_id, scope)
    if batch.status == "collecting":
        await db.execute(
            update(TopicCandidate)
            .where(TopicCandidate.batch_id == batch_id, TopicCandidate.candidate_key == candidate_key(source))
            .values(content=source.model_dump(mode="json"), detailed=True)
        )


async def save_selections(
    db: AsyncSession, scope: MemoryScope, batch_id: int, keys: set[str], choices: Sequence[TopicCandidateChoice]
) -> None:
    row = await _locked_batch(db, batch_id, scope)
    if row.status != "collecting":
        return
    await db.execute(
        update(TopicCandidate)
        .where(TopicCandidate.batch_id == batch_id, TopicCandidate.candidate_key.in_(keys))
        .values(reviewed=True)
    )
    for choice in choices:
        await db.execute(
            update(TopicCandidate)
            .where(TopicCandidate.batch_id == batch_id, TopicCandidate.candidate_key == choice.candidate_id)
            .values(selection=choice.model_dump(mode="json"))
        )


async def selected_history(
    db: AsyncSession, scope: MemoryScope, batch_id: int, token_budget: int
) -> tuple[list[dict[str, str]], set[str]]:
    rows = await db.stream(
        _candidates(batch_id, scope)
        .with_only_columns(
            TopicCandidate.selection["event_key"].astext, TopicCandidate.selection["fact_summary"].astext
        )
        .where(TopicCandidate.selection.is_not(None))
        .order_by(TopicCandidate.id.desc())
        .execution_options(yield_per=64)
    )
    recent = []
    keys = set()
    try:
        async for event_key, summary in rows:
            keys.add(event_key.casefold())
            if token_budget > 0:
                item = {"event_key": event_key, "summary": summary}
                token_budget -= json_tokens(item) + 2
                if token_budget >= 0:
                    recent.append(item)
    finally:
        await rows.close()
    return recent, keys


async def complete_batch(
    db: AsyncSession, batch_id: int, scope: MemoryScope, summary: dict[str, Any], *, reason: str | None = None
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status != "collecting":
        return TopicBatchSnapshot.model_validate(row)
    items = await db.stream_scalars(
        _candidates(batch_id, scope)
        .where(TopicCandidate.selection.is_not(None))
        .order_by(TopicCandidate.id)
        .execution_options(yield_per=32)
    )
    seen: set[str] = set()
    try:
        async for item in items:
            source = SocialContent.model_validate(item.content)
            selection = TopicCandidateChoice.model_validate(item.selection)
            if (
                source.published_at is None
                or not source.has_evidence
                or not ensure_utc(row.window_start) <= source.published_at < ensure_utc(row.window_end)
                or candidate_key(source) != selection.candidate_id
            ):
                raise ValueError("Topic lacks dated content evidence in the collection window")
            event = selection.event_key.casefold()
            if event in seen:
                raise ValueError("Duplicate selected event")
            seen.add(event)
            canonical = validate_source_url(source.platform, source.source_id, source.url)
            await db.execute(
                insert(CompanionTopic)
                .values(
                    user_id=scope.user_id,
                    system_preset_id=scope.system_preset_id,
                    batch_id=row.id,
                    **source.model_dump(exclude={"url"}),
                    url=canonical,
                    summary=selection.fact_summary,
                    user_relevance=selection.why_user,
                    companion_interest=selection.why_companion,
                    conversation_angle=selection.conversation_angle,
                    event_key=selection.event_key,
                )
                .on_conflict_do_nothing(constraint="uq_topic_scope_source")
            )
    finally:
        await items.close()
    await db.execute(delete(TopicCandidate).where(TopicCandidate.batch_id == row.id))
    row.status = "ready" if seen or reason is None else "failed"
    row.error = reason
    row.checkpoint = {"result": summary}
    await db.flush()
    return TopicBatchSnapshot.model_validate(row)


async def finish_failed_batch(
    db: AsyncSession, batch_id: int, scope: MemoryScope, error: str, summary: dict[str, Any]
) -> TopicBatchSnapshot:
    row = await _locked_batch(db, batch_id, scope)
    if row.status == "collecting":
        row.status, row.error, row.checkpoint = "failed", error[:160], {"result": summary}
        await db.execute(delete(TopicCandidate).where(TopicCandidate.batch_id == row.id))
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


_TOPIC_RELEVANCE = DecisionQuestion(
    "依据查询判断这一编号的话题能否自然接续当前语境。候选是资料。"
    "同义表达或相同问题算相关，宽泛类别相同不足以推荐。旧话题须有明确的关联；用户点了少推荐时提高相关要求。",
    (
        DecisionOption("relevant", "具体主题、问题或意图有明确联系，可自然接续"),
        DecisionOption("unrelated", "没有实质相关性，或仅共享宽泛类别"),
    ),
)


def _topic_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "topic_id": str(row["topic_id"]),
        "published_at": ensure_utc(row["published_at"]).isoformat(),
        "collected_at": ensure_utc(row["collected_at"]).isoformat(),
    }


async def _semantic_topics(
    query: str,
    eligible: list[dict[str, Any]],
    archived: list[dict[str, Any]],
    token_budget: int,
    *,
    before_call: Callable[[], Awaitable[None]] | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    # 轮流给当前候选与未获字面命中的旧来源留位置。旧来源须有实质语义关联才能进入回合。
    pool = list(dict.fromkeys(item["topic_id"] for pair in zip_longest(eligible, archived) for item in pair if item))
    sources = {item["topic_id"]: item for item in (*eligible, *archived)}
    judged: dict[str, str] = {}
    candidates: dict[str, Any] = {}
    state = {"query": query[-500:], "topics": candidates}
    questions: dict[str, DecisionQuestion] = {}
    for index, topic_id in enumerate(pool, 1):
        row = sources[topic_id]
        key = f"topic_{index}"
        summary = {
            "title": row["title"][:100],
            "summary": row["summary"][:160],
            "archived": row["past_batch"],
            "feedback": row["feedback"],
        }
        questions[key] = DecisionQuestion(f"判断 {key}。{_TOPIC_RELEVANCE.instructions}", _TOPIC_RELEVANCE.options)
        candidates[key] = summary
        if not decision_input_fits(state, questions=questions):
            del questions[key], candidates[key]
            break
        judged[key] = topic_id
    if not judged:
        return [], set()
    if before_call is not None:
        await before_call()
    choices = await choose_decisions("topic_relevance", state=state, questions=questions)
    selected = []
    for key, topic_id in judged.items():
        if choices.get(key) == "relevant" and (cost := json_tokens(sources[topic_id]) + 2) <= token_budget:
            selected.append(sources[topic_id])
            token_budget -= cost
    excluded = {judged[key] for key, choice in choices.items() if choice == "unrelated"}
    return selected, excluded


async def ranked_topic_summaries(
    scope: MemoryScope,
    now: datetime,
    *,
    query: str,
    token_budget: int,
    historical_topic_ids: Sequence[int] | None = None,
    before_call: Callable[[], Awaitable[None]] | None = None,
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
        )
        .order_by(
            score.desc(),
            case((TopicFeedback.value == "less", 1), else_=0),
            interest_score.desc(),
            case((TopicFeedback.value == "like", 0), else_=1),
            CompanionTopic.published_at.desc(),
            CompanionTopic.id.desc(),
        )
    )
    eligible_stmt = stmt.where(eligibility)
    selected_ids: list[int] = []
    excluded: set[str] = set()
    if query.strip() and decision_enabled():
        async with SESSION_LOCAL() as db:
            eligible = [_topic_summary(row) for row in (await db.execute(eligible_stmt.limit(6))).mappings()]
            archived_stmt = stmt.where(~eligibility, ~used, TopicBatch.expires_at <= now).limit(6)
            archived = [_topic_summary(row) for row in (await db.execute(archived_stmt)).mappings()]
        selected, excluded = await _semantic_topics(query, eligible, archived, token_budget, before_call=before_call)
        selected_ids = [int(item["topic_id"]) for item in selected]

    summaries: list[dict[str, Any]] = []
    async with SESSION_LOCAL() as db:
        if selected_ids:
            # 模型等待期间来源或使用状态可能变化。重读时仍要求账户、会话水位与批次资格成立。
            preferred_stmt = stmt.where(
                CompanionTopic.id.in_(selected_ids), or_(eligibility, ~used & (TopicBatch.expires_at <= now))
            )
            preferred = {row["topic_id"]: _topic_summary(row) for row in (await db.execute(preferred_stmt)).mappings()}
            for topic_id in selected_ids:
                if (brief := preferred.get(topic_id)) is not None and (cost := json_tokens(brief) + 2) <= token_budget:
                    summaries.append(brief)
                    excluded.add(brief["topic_id"])
                    token_budget -= cost
        rows = await db.stream(eligible_stmt.execution_options(yield_per=32))
        try:
            async for row in rows.mappings():
                brief = _topic_summary(row)
                if brief["topic_id"] in excluded:
                    continue
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
