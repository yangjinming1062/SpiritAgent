"""夜间信息检索：固定采集窗口、逐次请求预算与可恢复的精选结果。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.accounts.locking import lock_account
from features.accounts.queries import nightly_account
from features.configuration.social_accounts import credential_is_current, load_social_credentials, mark_login_required
from features.configuration.social_schemas import SocialCredentialSnapshot
from features.topics.schemas import TopicSelection
from features.topics.store import (
    batch_topic_count,
    begin_batch,
    finish_failed_batch,
    load_batch,
    publish_batch,
    recent_topic_feedback,
    reserve_request,
    save_checkpoint,
)
from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import ContextOverflowError, fit_text_prefix, json_tokens, material_budget
from infrastructure.ai.user_config import UserLlmConfig, load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text, utc_now
from infrastructure.web.social_content import RequestGuard, SocialContent, SocialSourceError
from infrastructure.web.social_platforms import SocialPlatform, get_social_platform
from infrastructure.web.social_sources import create_social_client
from pydantic import BaseModel, ConfigDict, Field
from runtime.memory_scope import MemoryScope
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.nightly.prompts import NIGHTLY_JSON_REPAIR_TEXTS
from workflows.nightly.topic_prompts import TOPIC_SEARCH_TEXTS, TOPIC_SELECTION_TEXTS
from workflows.nightly.window import NightlyWindowClosedError, require_nightly_window


class DiscoveryDisabledError(LlmCallBlockedError):
    """启动或请求前发现账户或夜间政策不可用。"""


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
    source_revisions: dict[str, str] = Field(default_factory=dict)
    queries: list[_SearchRequest] = Field(default_factory=list)
    query_plan_done: bool = False
    search_index: int = 0
    candidates: list[SocialContent] = Field(default_factory=list)
    search_done: bool = False
    details: list[str] = Field(default_factory=list)
    detail_index: int = 0
    details_done: bool = False
    selections: list[_Selection] = Field(default_factory=list)
    selection_index: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    search_successes: int = 0
    platform_search_successes: dict[str, int] = Field(default_factory=dict)
    duplicate_candidates: int = 0
    outside_window_candidates: int = 0


class TopicDiscoveryResult(BaseModel):
    status: Literal["ok", "error", "skipped"]
    summary: dict[str, Any] = Field(default_factory=dict)


def _candidate_id(candidate: SocialContent) -> str:
    return f"{candidate.platform}:{candidate.source_id}"


def _inside_window(candidate: SocialContent, start: datetime, end: datetime) -> bool:
    return candidate.published_at is not None and start <= candidate.published_at < end


async def _configured(db: AsyncSession, scope: MemoryScope) -> None:
    account = await nightly_account(db, scope.user_id)
    if account is None or not account.is_active or not account.nightly_activity_enabled:
        raise DiscoveryDisabledError("夜间检索已关闭或账户不可用")


def _require_batch_lifetime(expires_at: datetime) -> None:
    if utc_now() >= expires_at:
        raise NightlyWindowClosedError("话题批次已过期")


async def _before_batch_call(scope: MemoryScope, timezone: ZoneInfo, expires_at: datetime) -> None:
    await require_nightly_window(timezone)
    async with SESSION_LOCAL() as db:
        await _configured(db, scope)
    _require_batch_lifetime(expires_at)


async def _save(scope: MemoryScope, batch_id: int, checkpoint: _Checkpoint) -> None:
    async with SESSION_LOCAL() as db:
        await save_checkpoint(db, batch_id, scope, checkpoint.model_dump(mode="json"))
        await db.commit()


async def _remote_call[T](
    scope: MemoryScope,
    batch_id: int,
    timezone: ZoneInfo,
    expires_at: datetime,
    platform: SocialPlatform,
    source_revision: str,
    kind: Literal["search", "detail"],
    operation: Callable[[RequestGuard], Awaitable[T]],
) -> T:
    async def before_request() -> None:
        await _before_batch_call(scope, timezone, expires_at)
        limit = (
            SETTINGS.topic_search_requests_per_night if kind == "search" else SETTINGS.topic_detail_requests_per_night
        )
        async with SESSION_LOCAL() as db:
            if not await credential_is_current(db, scope.user_id, platform, source_revision):
                raise SocialSourceError("Platform credentials changed", code=409, reason="configuration_changed")
            permitted = await reserve_request(db, batch_id, scope, kind=kind, limit=limit)
            await db.commit()
        if not permitted:
            raise _RequestBudgetExhausted(kind)

    for attempt in range(2):
        try:
            return await operation(before_request)
        except SocialSourceError as exc:
            if exc.reason == "login_required":
                async with SESSION_LOCAL() as db:
                    await mark_login_required(db, scope.user_id, platform, source_revision)
                    await db.commit()
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


def _parse_selections(raw: Any, candidates: set[str]) -> _SelectionPlan:
    plan = _SelectionPlan.model_validate(raw)
    ids = [item.candidate_id for item in plan.selections]
    events = [item.event_key.casefold() for item in plan.selections]
    if len(set(ids)) != len(ids) or len(set(events)) != len(events):
        raise LlmJsonValidationError("invalid_selection_count_or_duplicates", fields=("selections", "event_key"))
    if any(identifier not in candidates for identifier in ids):
        raise LlmJsonValidationError("unknown_candidate", fields=("candidate_id",))
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
        for item in checkpoint.selections
    ]


def _discovery_payload(context: dict[str, Any], room: int) -> dict[str, Any]:
    recent = []
    for item in context.get("recent_topics", []):
        cost = json_tokens(item) + 2
        if cost > room:
            break
        recent.append(item)
        room -= cost
    return {**context, "recent_topics": recent}


def _candidate_batch(candidates: list[SocialContent], available: int, output_room: int) -> list[dict[str, Any]]:
    batch = []
    for source in candidates:
        row = {
            "candidate_id": _candidate_id(source),
            **source.model_dump(mode="json", exclude={"body", "subtitle"}),
            "body": "",
            "subtitle": "",
            "body_excerpted": False,
            "subtitle_excerpted": False,
        }
        cost = json_tokens(row) + json_tokens(source.body) + json_tokens(source.subtitle) + 4
        if batch and (cost > available or output_room < 512):
            break
        text_room = available - json_tokens(row) - 4
        body = fit_text_prefix(source.body, text_room // 2 if source.subtitle else text_room)
        subtitle = fit_text_prefix(source.subtitle, text_room - json_tokens(body))
        if not (body.strip() or subtitle.strip()):
            break
        row.update(
            body=body,
            subtitle=subtitle,
            body_excerpted=len(body) < len(source.body),
            subtitle_excerpted=len(subtitle) < len(source.subtitle),
        )
        batch.append(row)
        available -= json_tokens(row) + 2
        # 每项输出包含摘要、关注理由、交流方向与事件键。
        output_room -= 512
    return batch


async def _select_candidates(
    scope: MemoryScope,
    batch_id: int,
    checkpoint: _Checkpoint,
    eligible: dict[str, SocialContent],
    config: UserLlmConfig,
    language: str,
    before_call: Callable[[], Awaitable[None]],
    diagnostics: list[LlmAttemptDiagnostic] | None,
) -> None:
    instructions = resolve_prompt_text(TOPIC_SELECTION_TEXTS, language)
    schema = _SelectionPlan.model_json_schema()
    base = {**checkpoint.context, "recent_topics": [], "candidates": []}
    seen_events = {item["event_key"].casefold() for item in checkpoint.context.get("recent_topics", [])}
    seen_events.update(item.event_key.casefold() for item in checkpoint.selections)
    candidates = list(eligible.values())
    batch_ids: set[str] = set()

    def selection_payload(provider: ProviderConfig) -> dict[str, Any]:
        nonlocal batch_ids
        room = material_budget((provider,), instructions, base, schema)
        recent = [
            {"event_key": item.event_key, "summary": item.fact_summary} for item in reversed(checkpoint.selections)
        ]
        payload = _discovery_payload(
            {**checkpoint.context, "recent_topics": [*recent, *checkpoint.context.get("recent_topics", [])]}, room // 3
        )
        remaining = candidates[checkpoint.selection_index :]
        output_room = provider.max_output_tokens or 0
        available = room - (json_tokens(payload["recent_topics"]) - json_tokens([]))
        batch = _candidate_batch(remaining, available, output_room)
        if not batch and payload["recent_topics"]:
            payload["recent_topics"] = []
            batch = _candidate_batch(remaining, room, output_room)
        if not batch:
            raise ContextOverflowError("模型窗口无法容纳话题来源依据")
        batch_ids = {item["candidate_id"] for item in batch}
        return {**payload, "candidates": batch}

    while checkpoint.selection_index < len(candidates):
        plan = await call_llm_json(
            config,
            instructions,
            selection_payload,
            schema=schema,
            schema_name="topic_selection",
            parse_output=lambda value: _parse_selections(value, batch_ids),
            repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
            reasoning_effort=NIGHTLY_REASONING_EFFORT,
            before_call=before_call,
            diagnostics=diagnostics,
        )
        for selected in plan.selections:
            event = selected.event_key.casefold()
            if event not in seen_events:
                seen_events.add(event)
                checkpoint.selections.append(selected)
        checkpoint.selection_index += len(batch_ids)
        await _save(scope, batch_id, checkpoint)


async def _collect(
    scope: MemoryScope,
    batch_id: int,
    timezone: ZoneInfo,
    start: datetime,
    end: datetime,
    expires_at: datetime,
    checkpoint: _Checkpoint,
    connections: dict[str, SocialCredentialSnapshot],
) -> None:
    clients = {}
    for platform, connection in connections.items():
        if checkpoint.source_revisions.get(platform) != connection.revision:
            continue
        try:
            clients[platform] = create_social_client(platform, connection.credentials)
        except ValueError:
            checkpoint.errors.append({"platform": platform, "operation": "configure", "reason": "source_unavailable"})
    candidate_limit = SETTINGS.topic_candidate_limit
    while (
        not checkpoint.search_done
        and checkpoint.search_index < len(checkpoint.queries)
        and len(checkpoint.candidates) < candidate_limit
    ):
        request = checkpoint.queries[checkpoint.search_index]
        try:
            client = clients.get(request.platform)
            if client is None:
                raise SocialSourceError("Platform credentials changed", code=409, reason="configuration_changed")
            page = await _remote_call(
                scope,
                batch_id,
                timezone,
                expires_at,
                request.platform,
                checkpoint.source_revisions[request.platform],
                "search",
                lambda guard: client.search(
                    request.query, window_start=start, window_end=end, cursor=request.cursor, before_request=guard
                ),
            )
        except _RequestBudgetExhausted:
            break
        except SocialSourceError as exc:
            checkpoint.errors.append(
                {"platform": request.platform, "operation": "search", "code": exc.code, "reason": exc.reason}
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
            client = clients.get(candidate.platform)
            if client is None:
                raise SocialSourceError("Platform credentials changed", code=409, reason="configuration_changed")
            detailed = await _remote_call(
                scope,
                batch_id,
                timezone,
                expires_at,
                candidate.platform,
                checkpoint.source_revisions[candidate.platform],
                "detail",
                lambda guard: client.detail(candidate, before_request=guard),
            )
        except _RequestBudgetExhausted:
            break
        except SocialSourceError as exc:
            checkpoint.errors.append(
                {"platform": candidate.platform, "operation": "detail", "code": exc.code, "reason": exc.reason}
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
    activity_date = target_date + timedelta(days=1)
    available_from = datetime.combine(activity_date, time.min, timezone).astimezone(UTC)
    expires_at = datetime.combine(activity_date + timedelta(days=1), time.min, timezone).astimezone(UTC)
    async with SESSION_LOCAL() as db:
        await _configured(db, scope)
        connections = await load_social_credentials(db, scope.user_id)
        batch = await load_batch(db, scope, activity_date)
        if batch is None:
            await require_nightly_window(timezone)
            if not connections:
                return TopicDiscoveryResult(status="skipped", summary={"reason": "unconfigured"})
            batch = await begin_batch(
                db, scope, activity_date, available_from=available_from, expires_at=expires_at, started_at=utc_now()
            )
        recent = (
            await recent_topic_feedback(db, scope, since=batch.started_at - timedelta(days=30), limit=None)
            if not batch.checkpoint
            else []
        )
        await db.commit()
    checkpoint = (
        _Checkpoint.model_validate(batch.checkpoint)
        if batch.checkpoint
        else _Checkpoint(
            source_revisions={platform: connection.revision for platform, connection in connections.items()},
            context={**context, "recent_topics": recent},
        )
    )
    if batch.status == "failed":
        return TopicDiscoveryResult(status="error", summary={"batch_id": batch.id, "reason": batch.error})
    if batch.status == "ready":
        async with SESSION_LOCAL() as db:
            selected_count = await batch_topic_count(db, scope, batch.id)
        return TopicDiscoveryResult(
            status="ok",
            summary={"batch_id": batch.id, "selected": selected_count, "recovered": True},
        )
    await _save(scope, batch.id, checkpoint)
    try:
        if not checkpoint.source_revisions and not checkpoint.candidates:
            raise DiscoveryDisabledError("未配置可用的资讯来源")
        config = await load_user_llm_config(scope.user_id, AiTaskKey.nightly)
        before_call = partial(_before_batch_call, scope, timezone, batch.expires_at)
        if not checkpoint.query_plan_done:
            limit = SETTINGS.topic_search_requests_per_night
            if limit and SETTINGS.topic_candidate_limit:
                platforms = tuple(get_social_platform(platform).id for platform in checkpoint.source_revisions)
                instructions = resolve_prompt_text(TOPIC_SEARCH_TEXTS, language)
                query_schema = _query_schema(platforms, limit)
                search_payload = {
                    **checkpoint.context,
                    "available_platforms": list(platforms),
                    "max_queries": limit,
                    "window_start": batch.window_start.isoformat(),
                    "window_end": batch.window_end.isoformat(),
                }
                room = material_budget(
                    config.chain, instructions, {**search_payload, "recent_topics": []}, query_schema
                )
                plan = await call_llm_json(
                    config,
                    instructions,
                    _discovery_payload(search_payload, room),
                    schema=query_schema,
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
                connections,
            )
        eligible = {
            _candidate_id(candidate): candidate
            for candidate in checkpoint.candidates
            if candidate.has_evidence and _inside_window(candidate, batch.window_start, batch.window_end)
        }
        if (
            checkpoint.queries
            and not checkpoint.search_successes
            and any(error.get("reason") != "configuration_changed" for error in checkpoint.errors)
        ):
            raise SocialSourceError("All topic searches failed")
        await _select_candidates(scope, batch.id, checkpoint, eligible, config, language, before_call, diagnostics)
        selections = _selected(checkpoint, eligible)
        async with SESSION_LOCAL() as db:
            # 已受理的精选可跨过夜间窗口结算，账户政策与批次有效期在提交事务内核对。
            await lock_account(db, scope.user_id)
            await _configured(db, scope)
            _require_batch_lifetime(batch.expires_at)
            completed = await publish_batch(db, batch.id, scope, selections)
            selected_count = await batch_topic_count(db, scope, batch.id)
            await db.commit()
        return TopicDiscoveryResult(
            status="ok",
            summary={
                "batch_id": batch.id,
                "candidates": len(checkpoint.candidates),
                "eligible": len(eligible),
                "platforms": list(checkpoint.source_revisions),
                "selected": selected_count,
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
        async with SESSION_LOCAL() as db:
            await finish_failed_batch(db, batch.id, scope, error)
            await db.commit()
        if isinstance(exc, NightlyWindowClosedError | DiscoveryDisabledError):
            return TopicDiscoveryResult(status="skipped", summary={"batch_id": batch.id, "reason": error})
        raise
