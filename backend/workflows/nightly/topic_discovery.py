"""夜间信息检索：固定采集窗口、逐次请求预算与可恢复的精选结果。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.accounts.queries import nightly_account
from features.configuration.settings.values import get_user_setting
from features.topics.schemas import TopicSelection
from features.topics.store import (
    begin_batch,
    finish_failed_batch,
    load_batch_topics,
    publish_batch,
    recent_topic_feedback,
    reserve_request,
    save_checkpoint,
)
from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import session_scope
from infrastructure.text import resolve_prompt_text, utc_now
from infrastructure.web.social_content import RequestGuard, SocialContent, SocialPlatform, SocialSourceError
from infrastructure.web.social_sources import available_platforms, create_social_client, source_configured
from pydantic import BaseModel, ConfigDict, Field
from runtime.memory_scope import MemoryScope

from workflows.nightly.prompts import NIGHTLY_JSON_REPAIR_TEXTS
from workflows.nightly.topic_prompts import TOPIC_SEARCH_TEXTS, TOPIC_SELECTION_TEXTS
from workflows.nightly.window import NightlyWindowClosedError, require_nightly_window


class DiscoveryDisabledError(LlmCallBlockedError):
    """启动或请求前发现账户/信息检索开关不可用。"""


class _RequestBudgetExhausted(RuntimeError):
    pass


class _Query(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: SocialPlatform
    query: str = Field(min_length=1, max_length=200)


class _QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[_Query]


class _SearchRequest(_Query):
    cursor: dict[str, Any] | None = None


class _Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    candidate_id: str = Field(min_length=1, max_length=128)
    fact_summary: str = Field(min_length=1, max_length=2000)
    why_user: str = Field(min_length=1, max_length=800)
    why_companion: str = Field(min_length=1, max_length=800)
    conversation_angle: str = Field(min_length=1, max_length=800)
    event_key: str = Field(min_length=1, max_length=120)


class _SelectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[_Selection]


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: dict[str, Any]
    source_backend: Literal["free", "tikhub"] = "free"
    queries: list[_SearchRequest] = Field(default_factory=list)
    query_plan_done: bool = False
    search_index: int = 0
    candidates: list[SocialContent] = Field(default_factory=list)
    search_done: bool = False
    details: list[str] = Field(default_factory=list)
    detail_index: int = 0
    details_done: bool = False
    selections: list[_Selection] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    search_successes: int = 0
    platform_search_successes: dict[str, int] = Field(default_factory=dict)
    duplicate_candidates: int = 0
    outside_window_candidates: int = 0


class TopicDiscoveryResult(BaseModel):
    status: Literal["ok", "error", "skipped"]
    topics: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


def _candidate_id(candidate: SocialContent) -> str:
    return f"{candidate.platform}:{candidate.source_id}"


def _inside_window(candidate: SocialContent, start: datetime, end: datetime) -> bool:
    return candidate.published_at is not None and start <= candidate.published_at < end


async def _configured(scope: MemoryScope) -> None:
    async with session_scope() as db:
        account = await nightly_account(db, scope.user_id)
        enabled = await get_user_setting(db, scope.user_id, "companion.information_discovery_enabled")
    if account is None or not account.is_active or not account.nightly_activity_enabled or enabled is not True:
        raise DiscoveryDisabledError("主动信息检索已关闭或账户不可用")
    if not source_configured():
        raise DiscoveryDisabledError("未配置可用的社交资讯来源")


async def _enabled(scope: MemoryScope, timezone: ZoneInfo) -> None:
    await require_nightly_window(timezone)
    await _configured(scope)


def _require_batch_lifetime(expires_at: datetime) -> None:
    if utc_now() >= expires_at:
        raise NightlyWindowClosedError("话题批次已过期")


async def _before_batch_call(
    scope: MemoryScope,
    timezone: ZoneInfo,
    expires_at: datetime,
    source_backend: Literal["free", "tikhub"],
) -> None:
    await _enabled(scope, timezone)
    _require_batch_lifetime(expires_at)
    if SETTINGS.social_search_backend != source_backend:
        raise DiscoveryDisabledError("采集方式已变更")


async def _save(scope: MemoryScope, batch_id: int, checkpoint: _Checkpoint) -> None:
    async with session_scope() as db:
        await save_checkpoint(db, batch_id, scope, checkpoint.model_dump(mode="json"))
        await db.commit()


async def _remote_call[T](
    scope: MemoryScope,
    batch_id: int,
    timezone: ZoneInfo,
    expires_at: datetime,
    platform: SocialPlatform,
    source_backend: Literal["free", "tikhub"],
    kind: Literal["search", "detail"],
    operation: Callable[[RequestGuard], Awaitable[T]],
) -> T:
    async def before_request() -> None:
        await _before_batch_call(scope, timezone, expires_at, source_backend)
        if platform not in available_platforms():
            raise DiscoveryDisabledError("采集方式或平台配置已变更")
        limit = (
            SETTINGS.topic_search_requests_per_night if kind == "search" else SETTINGS.topic_detail_requests_per_night
        )
        async with session_scope() as db:
            permitted = await reserve_request(db, batch_id, scope, kind=kind, limit=limit)
            await db.commit()
        if not permitted:
            raise _RequestBudgetExhausted(kind)

    for attempt in range(2):
        await _before_batch_call(scope, timezone, expires_at, source_backend)
        try:
            return await operation(before_request)
        except SocialSourceError as exc:
            # 限流/额度与请求参数失败留给下一夜；只有网络或服务故障做一次预算内重试。
            if attempt or exc.code is not None and exc.code < 500:
                raise
            await asyncio.sleep(1)
    raise RuntimeError("Unreachable social source attempt state")


def _query_schema(platforms: tuple[SocialPlatform, ...], limit: int) -> dict[str, Any]:
    schema = _QueryPlan.model_json_schema()
    schema["$defs"]["_Query"]["properties"]["platform"]["enum"] = list(platforms)
    schema["properties"]["queries"]["maxItems"] = limit
    return schema


def _parse_queries(raw: Any, limit: int, platforms: tuple[SocialPlatform, ...]) -> _QueryPlan:
    plan = _QueryPlan.model_validate(raw)
    if len(plan.queries) > limit:
        raise LlmJsonValidationError("too_many_queries", fields=("queries",))
    if any(query.platform not in platforms for query in plan.queries):
        raise LlmJsonValidationError("unsupported_platform", fields=("platform",))
    keys = {(query.platform, query.query.casefold()) for query in plan.queries}
    if len(keys) != len(plan.queries):
        raise LlmJsonValidationError("duplicate_queries", fields=("queries",))
    return plan


def _parse_selections(
    raw: Any,
    candidates: dict[str, SocialContent],
    limit: int,
    previous_events: set[str],
) -> _SelectionPlan:
    plan = _SelectionPlan.model_validate(raw)
    ids = [item.candidate_id for item in plan.selections]
    events = [item.event_key.casefold() for item in plan.selections]
    if len(ids) > limit or len(set(ids)) != len(ids) or len(set(events)) != len(events):
        raise LlmJsonValidationError("invalid_selection_count_or_duplicates", fields=("selections", "event_key"))
    if any(identifier not in candidates for identifier in ids):
        raise LlmJsonValidationError("unknown_candidate", fields=("candidate_id",))
    if previous_events.intersection(events):
        raise LlmJsonValidationError("repeated_event_without_progress", fields=("event_key",))
    return plan


def _selected(checkpoint: _Checkpoint, eligible: dict[str, SocialContent]) -> list[TopicSelection]:
    return [
        TopicSelection(
            source=eligible[item.candidate_id],
            summary=item.fact_summary,
            user_relevance=item.why_user,
            companion_interest=item.why_companion,
            conversation_angle=item.conversation_angle,
            event_key=item.event_key,
        )
        for item in checkpoint.selections or []
    ]


async def _collect(
    scope: MemoryScope,
    batch_id: int,
    timezone: ZoneInfo,
    start: datetime,
    end: datetime,
    expires_at: datetime,
    checkpoint: _Checkpoint,
) -> None:
    client = create_social_client()
    candidate_limit = SETTINGS.topic_candidate_limit
    while (
        not checkpoint.search_done
        and checkpoint.search_index < len(checkpoint.queries)
        and len(checkpoint.candidates) < candidate_limit
    ):
        request = checkpoint.queries[checkpoint.search_index]
        try:
            page = await _remote_call(
                scope,
                batch_id,
                timezone,
                expires_at,
                request.platform,
                checkpoint.source_backend,
                "search",
                lambda guard: client.search(
                    request.platform,
                    request.query,
                    window_start=start,
                    window_end=end,
                    cursor=request.cursor,
                    before_request=guard,
                ),
            )
        except _RequestBudgetExhausted:
            break
        except SocialSourceError as exc:
            checkpoint.errors.append(
                {"platform": request.platform, "operation": "search", "code": exc.code, "reason": exc.reason},
            )
        else:
            checkpoint.search_successes += 1
            checkpoint.platform_search_successes[request.platform] = (
                checkpoint.platform_search_successes.get(request.platform, 0) + 1
            )
            known = {_candidate_id(candidate) for candidate in checkpoint.candidates}
            for candidate in page.candidates:
                if candidate.platform != request.platform:
                    continue
                identifier = _candidate_id(candidate)
                if identifier in known:
                    checkpoint.duplicate_candidates += 1
                    continue
                if candidate.published_at is not None and not _inside_window(candidate, start, end):
                    checkpoint.outside_window_candidates += 1
                    continue
                checkpoint.candidates.append(candidate)
                known.add(identifier)
                if len(checkpoint.candidates) >= candidate_limit:
                    break
            if page.next_cursor and len(checkpoint.queries) < SETTINGS.topic_search_requests_per_night:
                next_request = _SearchRequest(platform=request.platform, query=request.query, cursor=page.next_cursor)
                if next_request not in checkpoint.queries:
                    checkpoint.queries.append(next_request)
        checkpoint.search_index += 1
        await _save(scope, batch_id, checkpoint)
    checkpoint.search_done = True
    await _save(scope, batch_id, checkpoint)

    if not checkpoint.details:
        candidates = sorted(
            checkpoint.candidates,
            key=lambda candidate: not (candidate.published_at is None or not candidate.has_evidence),
        )
        checkpoint.details = [
            _candidate_id(candidate)
            for candidate in candidates
            if candidate.published_at is None
            or not candidate.has_evidence
            or candidate.content_type == "video"
            and not candidate.subtitle
        ]
        await _save(scope, batch_id, checkpoint)
    by_id = {_candidate_id(candidate): index for index, candidate in enumerate(checkpoint.candidates)}
    while checkpoint.detail_index < len(checkpoint.details):
        index = by_id[checkpoint.details[checkpoint.detail_index]]
        candidate = checkpoint.candidates[index]
        try:
            detailed = await _remote_call(
                scope,
                batch_id,
                timezone,
                expires_at,
                candidate.platform,
                checkpoint.source_backend,
                "detail",
                lambda guard: client.detail(candidate, before_request=guard),
            )
        except _RequestBudgetExhausted:
            break
        except SocialSourceError as exc:
            checkpoint.errors.append(
                {"platform": candidate.platform, "operation": "detail", "code": exc.code, "reason": exc.reason},
            )
        else:
            if detailed is not None and _candidate_id(detailed) == _candidate_id(candidate):
                checkpoint.candidates[index] = detailed
        checkpoint.detail_index += 1
        await _save(scope, batch_id, checkpoint)
    checkpoint.details_done = True
    await _save(scope, batch_id, checkpoint)


async def discover_topics(
    scope: MemoryScope,
    target_date: date,
    timezone: ZoneInfo,
    context: dict[str, Any],
    language: str,
    *,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> TopicDiscoveryResult:
    """阶段检查点归话题模块，夜间日志只保存无正文的结果统计。"""
    await _enabled(scope, timezone)
    activity_date = target_date + timedelta(days=1)
    available_from = datetime.combine(activity_date, time.min, timezone).astimezone(UTC)
    expires_at = datetime.combine(activity_date + timedelta(days=1), time.min, timezone).astimezone(UTC)
    async with session_scope() as db:
        batch = await begin_batch(
            db,
            scope,
            activity_date,
            available_from=available_from,
            expires_at=expires_at,
            started_at=utc_now(),
        )
        recent = await recent_topic_feedback(db, scope, since=batch.started_at - timedelta(days=30), limit=None)
        await db.commit()
    checkpoint = (
        _Checkpoint.model_validate(batch.checkpoint)
        if batch.checkpoint
        else _Checkpoint(
            source_backend=SETTINGS.social_search_backend,
            context={
                **context,
                "recent_topics": [
                    {key: value for key, value in topic.items() if key not in {"body", "subtitle"}} for topic in recent
                ],
            },
        )
    )
    eligible = {
        _candidate_id(candidate): candidate
        for candidate in checkpoint.candidates
        if candidate.has_evidence and _inside_window(candidate, batch.window_start, batch.window_end)
    }
    if batch.status == "failed":
        return TopicDiscoveryResult(status="error", summary={"batch_id": batch.id, "reason": batch.error})
    if batch.status == "ready":
        async with session_scope() as db:
            topics = await load_batch_topics(db, scope, batch.id)
        return TopicDiscoveryResult(
            status="ok",
            topics=[topic.model_dump(mode="json") for topic in topics],
            summary={"batch_id": batch.id, "selected": len(topics), "recovered": True},
        )
    await _save(scope, batch.id, checkpoint)
    try:
        if checkpoint.source_backend != SETTINGS.social_search_backend:
            raise DiscoveryDisabledError("采集方式已变更")
        config = await load_user_llm_config(scope.user_id, AiTaskKey.nightly)
        before_call = partial(_before_batch_call, scope, timezone, batch.expires_at, checkpoint.source_backend)
        if not checkpoint.query_plan_done:
            limit = SETTINGS.topic_search_requests_per_night
            if limit and SETTINGS.topic_candidate_limit and SETTINGS.topic_reserve_limit:
                platforms = available_platforms()
                plan = await call_llm_json(
                    config,
                    resolve_prompt_text(TOPIC_SEARCH_TEXTS, language),
                    {
                        **checkpoint.context,
                        "available_platforms": list(platforms),
                        "max_queries": limit,
                        "window_start": batch.window_start.isoformat(),
                        "window_end": batch.window_end.isoformat(),
                    },
                    schema=_query_schema(platforms, limit),
                    schema_name="topic_search_plan",
                    parse_output=partial(_parse_queries, limit=limit, platforms=platforms),
                    repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
                    reasoning_effort=NIGHTLY_REASONING_EFFORT,
                    before_call=before_call,
                    diagnostics=diagnostics,
                )
                checkpoint.queries = [_SearchRequest(**item.model_dump()) for item in plan.queries]
            checkpoint.query_plan_done = True
            await _save(scope, batch.id, checkpoint)
        if not checkpoint.details_done:
            await _collect(
                scope,
                batch.id,
                timezone,
                batch.window_start,
                batch.window_end,
                batch.expires_at,
                checkpoint,
            )
        eligible = {
            _candidate_id(candidate): candidate
            for candidate in checkpoint.candidates
            if candidate.has_evidence and _inside_window(candidate, batch.window_start, batch.window_end)
        }
        if checkpoint.queries and not checkpoint.search_successes:
            raise SocialSourceError("All topic searches failed")
        if checkpoint.selections is None:
            if eligible and SETTINGS.topic_reserve_limit:
                previous_events = {
                    item["event_key"].casefold()
                    for item in checkpoint.context.get("recent_topics", [])
                    if isinstance(item.get("event_key"), str)
                }
                plan = await call_llm_json(
                    config,
                    resolve_prompt_text(TOPIC_SELECTION_TEXTS, language),
                    {
                        **checkpoint.context,
                        "max_topics": SETTINGS.topic_reserve_limit,
                        "candidates": [
                            {"candidate_id": key, **candidate.model_dump(mode="json")}
                            for key, candidate in eligible.items()
                        ],
                    },
                    schema=_SelectionPlan.model_json_schema(),
                    schema_name="topic_selection",
                    parse_output=partial(
                        _parse_selections,
                        candidates=eligible,
                        limit=SETTINGS.topic_reserve_limit,
                        previous_events=previous_events,
                    ),
                    repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
                    reasoning_effort=NIGHTLY_REASONING_EFFORT,
                    before_call=before_call,
                    diagnostics=diagnostics,
                )
                checkpoint.selections = plan.selections
            else:
                checkpoint.selections = []
            await _save(scope, batch.id, checkpoint)
        selections = _selected(checkpoint, eligible)
        # 已提交请求的结果先留检查点；停用或跨过当地午夜后不再发布新储备。
        await _configured(scope)
        _require_batch_lifetime(batch.expires_at)
        if SETTINGS.social_search_backend != checkpoint.source_backend:
            raise DiscoveryDisabledError("采集方式已变更")
        async with session_scope() as db:
            completed = await publish_batch(db, batch.id, scope, selections, limit=SETTINGS.topic_reserve_limit)
            topics = await load_batch_topics(db, scope, batch.id)
            await db.commit()
        return TopicDiscoveryResult(
            status="ok",
            topics=[topic.model_dump(mode="json") for topic in topics],
            summary={
                "batch_id": batch.id,
                "candidates": len(checkpoint.candidates),
                "eligible": len(eligible),
                "source_backend": checkpoint.source_backend,
                "selected": len(topics),
                "search_requests": completed.search_requests,
                "detail_requests": completed.detail_requests,
                "platform_errors": checkpoint.errors,
                "platform_search_successes": checkpoint.platform_search_successes,
                "duplicate_candidates": checkpoint.duplicate_candidates,
                "outside_window_candidates": checkpoint.outside_window_candidates,
            },
        )
    except Exception as exc:
        error = (
            "window_closed"
            if isinstance(exc, NightlyWindowClosedError)
            else "disabled"
            if isinstance(exc, DiscoveryDisabledError)
            else type(exc).__name__
        )
        async with session_scope() as db:
            await finish_failed_batch(db, batch.id, scope, error)
            await db.commit()
        if isinstance(exc, NightlyWindowClosedError | DiscoveryDisabledError):
            return TopicDiscoveryResult(status="skipped", summary={"batch_id": batch.id, "reason": error})
        raise
