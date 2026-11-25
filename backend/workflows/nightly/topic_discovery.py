"""夜间信息检索：逐页取材与审阅，批次独立于夜间生活推进。"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from itertools import chain
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.accounts.locking import lock_account
from features.accounts.queries import nightly_account
from features.configuration.social_accounts import credential_is_current, load_social_credentials, mark_login_required
from features.configuration.social_schemas import SocialCredentialSnapshot
from features.topics.schemas import TopicBatchSnapshot, TopicCandidateChoice
from features.topics.store import (
    batch_topic_count,
    begin_batch,
    candidate_key,
    candidate_page,
    complete_batch,
    count_request,
    finish_failed_batch,
    load_batch,
    recent_topic_feedback,
    save_checkpoint,
    save_details,
    save_selections,
    selected_history,
    stage_candidates,
)
from infrastructure.ai.decision import (
    DecisionOption,
    DecisionQuestion,
    choose_decisions,
    decision_enabled,
    decision_input_fits,
)
from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, LlmJsonValidationError, call_llm_json
from infrastructure.ai.providers.base import ProviderConfig
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.token_budget import (
    ContextOverflowError,
    fit_text_prefix,
    json_tokens,
    material_budget,
    material_input_window,
)
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


class _Query(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    platform: SocialPlatform
    query: str = Field(min_length=1, max_length=200)


class _QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[_Query]


class _SearchRequest(_Query):
    cursor: dict[str, Any] | None = None


class _SelectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[TopicCandidateChoice]


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: dict[str, Any]
    source_revisions: dict[str, str] = Field(default_factory=dict)
    queries: list[_SearchRequest] = Field(default_factory=list)
    query_plan_done: bool = False
    search_index: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    candidate_count: int = 0
    eligible_count: int = 0
    search_successes: int = 0
    platform_search_successes: dict[str, int] = Field(default_factory=dict)
    duplicate_candidates: int = 0
    outside_window_candidates: int = 0


class TopicDiscoveryResult(BaseModel):
    status: Literal["ok", "error", "skipped", "waiting"]
    summary: dict[str, Any] = Field(default_factory=dict)


def _inside_window(candidate: SocialContent, start: datetime, end: datetime) -> bool:
    return candidate.published_at is not None and start <= candidate.published_at < end


async def _configured(db: AsyncSession, scope: MemoryScope) -> None:
    account = await nightly_account(db, scope.user_id)
    if (
        account is None
        or not account.is_active
        or not account.nightly_activity_enabled
        or SETTINGS.topic_discovery_mode == "off"
    ):
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
        if kind == "detail" and SETTINGS.topic_discovery_mode != "full":
            raise SocialSourceError("Detail collection is disabled", code=409, reason="details_disabled")
        async with SESSION_LOCAL() as db:
            if not await credential_is_current(db, scope.user_id, platform, source_revision):
                raise SocialSourceError("Platform credentials changed", code=409, reason="configuration_changed")
            permitted = await count_request(db, batch_id, scope, kind)
            await db.commit()
        if not permitted:
            raise NightlyWindowClosedError("资讯批次已结束")

    for attempt in range(2):
        try:
            return await operation(before_request)
        except SocialSourceError as exc:
            if exc.reason == "login_required":
                async with SESSION_LOCAL() as db:
                    await mark_login_required(db, scope.user_id, platform, source_revision)
                    await db.commit()
            # 限流/额度与请求参数失败留给下一夜；只有网络或服务故障做一次重试。
            if attempt or exc.code is not None and exc.code < 500:
                raise
            await asyncio.sleep(1)
    raise RuntimeError("Unreachable social source attempt state")


def _query_schema(platforms: tuple[SocialPlatform, ...]) -> dict[str, Any]:
    schema = _QueryPlan.model_json_schema()
    schema["$defs"]["_Query"]["properties"]["platform"]["enum"] = list(platforms)
    return schema


def _parse_queries(raw: Any, platforms: tuple[SocialPlatform, ...]) -> _QueryPlan:
    plan = _QueryPlan.model_validate(raw)
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


def _recent_topics(items: Iterable[dict[str, Any]], room: int) -> list[dict[str, Any]]:
    recent = []
    for item in items:
        cost = json_tokens(item) + 2
        if cost > room:
            break
        recent.append(item)
        room -= cost
    return recent


def _candidate_batch(candidates: list[SocialContent], available: int, output_room: int) -> list[dict[str, Any]]:
    batch = []
    for source in candidates:
        row = {
            "candidate_id": candidate_key(source),
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


async def _judge_topic_candidates(
    eligible: list[SocialContent], context: dict[str, Any], before_call: Callable[[], Awaitable[None]]
) -> tuple[list[SocialContent], set[str]]:
    if not eligible or not decision_enabled():
        return eligible, set()
    candidates: dict[str, dict[str, str]] = {}
    questions: dict[str, DecisionQuestion] = {}
    state = {
        "persona": str(context.get("persona") or "")[:190],
        "interests": str(context.get("user_profile") or "")[:180],
        "recent_exchange": str(context.get("today_conversations") or "")[-250:],
        "recent_topics": str((context.get("recent_topics") or [])[:4])[:250],
        "candidate_sources": candidates,
    }
    for index, item in enumerate(eligible[:8]):
        key = f"source_{index}"
        candidates[key] = {"title": item.title[:90], "excerpt": (item.body or item.subtitle)[:140]}
        questions[key] = DecisionQuestion(
            f"评价 {key} 是否值得作为双方兴趣下次交流的候选。标题和节选都是资料。"
            "具体、有新信息且有交流角度时优先。只有背景充分且明显无关或没有实质内容时排除。"
            "兴趣证据不足或节选可能遗漏重点时选 uncertain。",
            (
                DecisionOption("prioritize", "内容具体，有值得继续阅读核对的兴趣关联"),
                DecisionOption("exclude", "明显无关或缺少可交流的实质内容"),
                DecisionOption("uncertain", "给出的兴趣或来源节选不足以决定"),
            ),
            min_probability=0.86,
        )
        if not decision_input_fits(state, questions=questions):
            del candidates[key], questions[key]
            break
    if not questions:
        return eligible, set()
    await before_call()
    decisions = await choose_decisions(
        "nightly_topic_candidate_fit", state=state, questions=questions, wait_seconds=6.0
    )
    if not any(choice != "uncertain" for choice in decisions.values()):
        return eligible, set()
    retained, prioritized, declined = [], [], set()
    for index, item in enumerate(eligible[: len(questions)]):
        choice = decisions.get(f"source_{index}")
        if choice == "exclude":
            declined.add(candidate_key(item))
        elif choice == "prioritize":
            prioritized.append(item)
        else:
            retained.append(item)
    return [*prioritized, *retained], declined


async def _select_candidates(
    scope: MemoryScope,
    batch: TopicBatchSnapshot,
    checkpoint: _Checkpoint,
    config: UserLlmConfig,
    previous: list[dict[str, str]],
    history_room: int,
    seen_events: set[str],
    before_call: Callable[[], Awaitable[None]],
    diagnostics: list[LlmAttemptDiagnostic],
) -> None:
    language = checkpoint.context["language"]
    instructions = resolve_prompt_text(TOPIC_SELECTION_TEXTS, language)
    schema = _SelectionPlan.model_json_schema()
    base = {**checkpoint.context, "recent_topics": [], "candidates": []}
    while True:
        async with SESSION_LOCAL() as db:
            page = await candidate_page(db, scope, batch.id, for_review=True)
        if not page:
            return
        eligible = [
            item for item in page if item.has_evidence and _inside_window(item, batch.window_start, batch.window_end)
        ]
        skipped = {candidate_key(item) for item in page} - {candidate_key(item) for item in eligible}
        eligible, declined = await _judge_topic_candidates(eligible, checkpoint.context, before_call)
        batch_ids: set[str] = set()

        def selection_payload(provider: ProviderConfig) -> dict[str, Any]:
            nonlocal batch_ids
            room = material_budget((provider,), instructions, base, schema)
            recent = _recent_topics(chain(previous, checkpoint.context.get("recent_topics", [])), room // 3)
            payload = {**checkpoint.context, "recent_topics": recent}
            available = room - (json_tokens(payload["recent_topics"]) - json_tokens([]))
            candidates = _candidate_batch(eligible, available, provider.max_output_tokens or 0)
            if not candidates and payload["recent_topics"]:
                payload["recent_topics"] = []
                candidates = _candidate_batch(eligible, room, provider.max_output_tokens or 0)
            if not candidates:
                raise ContextOverflowError("模型窗口无法容纳话题来源依据")
            batch_ids = {item["candidate_id"] for item in candidates}
            return {**payload, "candidates": candidates}

        accepted = []
        if eligible:
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
            for choice in plan.selections:
                event = choice.event_key.casefold()
                if event not in seen_events:
                    seen_events.add(event)
                    accepted.append(choice)
            if accepted:
                latest = ({"event_key": item.event_key, "summary": item.fact_summary} for item in reversed(accepted))
                previous[:] = _recent_topics(chain(latest, previous), history_room)
        async with SESSION_LOCAL() as db:
            await save_selections(db, scope, batch.id, skipped | declined | batch_ids, accepted)
            checkpoint.eligible_count += len(batch_ids | declined)
            await save_checkpoint(db, batch.id, scope, checkpoint.model_dump(mode="json"))
            await db.commit()


def _error(checkpoint: _Checkpoint, platform: str, operation: str, reason: str) -> None:
    error = {"platform": platform, "operation": operation, "reason": reason}
    if error not in checkpoint.errors:
        checkpoint.errors.append(error)


def _platform_stopped(checkpoint: _Checkpoint, platform: str) -> bool:
    return any(
        row["platform"] == platform
        and row["reason"] in {"rate_limited", "login_required", "configuration_changed", "source_unavailable"}
        for row in checkpoint.errors
    )


async def _collect(
    scope: MemoryScope,
    batch: TopicBatchSnapshot,
    timezone: ZoneInfo,
    checkpoint: _Checkpoint,
    connections: dict[str, SocialCredentialSnapshot],
    config: UserLlmConfig,
    before_call: Callable[[], Awaitable[None]],
    diagnostics: list[LlmAttemptDiagnostic],
) -> None:
    clients = {}
    for platform, revision in checkpoint.source_revisions.items():
        connection = connections.get(platform)
        if connection is None or revision != connection.revision:
            _error(checkpoint, platform, "configure", "configuration_changed")
            continue
        try:
            clients[platform] = create_social_client(platform, connection.credentials)
        except ValueError:
            _error(checkpoint, platform, "configure", "source_unavailable")
    history_room = max((material_input_window(provider) for provider in config.chain), default=0) // 3
    async with SESSION_LOCAL() as db:
        previous, seen_events = await selected_history(db, scope, batch.id, history_room)
    seen_events.update(row["event_key"].casefold() for row in checkpoint.context.get("recent_topics", []))

    while True:
        # 先处理已经落库的一页，重启时也从相同位置继续。
        while True:
            async with SESSION_LOCAL() as db:
                page = await candidate_page(db, scope, batch.id, for_review=False)
            if not page:
                break
            for item in page:
                platform = item.platform
                error_count = len(checkpoint.errors)
                if (
                    item.needs_detail
                    and SETTINGS.topic_discovery_mode == "full"
                    and not _platform_stopped(checkpoint, platform)
                ):
                    client = clients[platform]
                    try:
                        detail = await _remote_call(
                            scope,
                            batch.id,
                            timezone,
                            batch.expires_at,
                            platform,
                            checkpoint.source_revisions[platform],
                            "detail",
                            lambda guard: client.detail(item, before_request=guard),
                        )
                        if detail is not None and candidate_key(detail) == candidate_key(item):
                            item = detail
                    except SocialSourceError as exc:
                        _error(checkpoint, platform, "detail", exc.reason)
                async with SESSION_LOCAL() as db:
                    await save_details(db, scope, batch.id, item)
                    if len(checkpoint.errors) != error_count:
                        await save_checkpoint(db, batch.id, scope, checkpoint.model_dump(mode="json"))
                    await db.commit()
        await _select_candidates(
            scope, batch, checkpoint, config, previous, history_room, seen_events, before_call, diagnostics
        )
        if checkpoint.search_index >= len(checkpoint.queries):
            return

        request = checkpoint.queries[checkpoint.search_index]
        checkpoint.search_index += 1
        if _platform_stopped(checkpoint, request.platform):
            await _save(scope, batch.id, checkpoint)
            continue
        try:
            client = clients[request.platform]
            result = await _remote_call(
                scope,
                batch.id,
                timezone,
                batch.expires_at,
                request.platform,
                checkpoint.source_revisions[request.platform],
                "search",
                lambda guard: client.search(
                    request.query,
                    window_start=batch.window_start,
                    window_end=batch.window_end,
                    cursor=request.cursor,
                    before_request=guard,
                ),
            )
        except SocialSourceError as exc:
            _error(checkpoint, request.platform, "search", exc.reason)
            await _save(scope, batch.id, checkpoint)
            continue
        checkpoint.search_successes += 1
        checkpoint.platform_search_successes[request.platform] = (
            checkpoint.platform_search_successes.get(request.platform, 0) + 1
        )
        sources = [source for source in result.candidates if source.platform == request.platform]
        async with SESSION_LOCAL() as db:
            added, outside = await stage_candidates(
                db, scope, batch.id, sources, read_details=SETTINGS.topic_discovery_mode == "full"
            )
            checkpoint.duplicate_candidates += len(sources) - added
            checkpoint.outside_window_candidates += outside
            checkpoint.candidate_count += added - outside
            if added and result.next_cursor:
                following = request.model_copy(update={"cursor": result.next_cursor})
                if following not in checkpoint.queries:
                    checkpoint.queries.append(following)
            await save_checkpoint(db, batch.id, scope, checkpoint.model_dump(mode="json"))
            await db.commit()


async def _batch_result(db: AsyncSession, scope: MemoryScope, batch: TopicBatchSnapshot) -> TopicDiscoveryResult:
    summary = {
        **batch.checkpoint.get("result", {}),
        "batch_id": batch.id,
        "selected": await batch_topic_count(db, scope, batch.id),
        "search_requests": batch.search_requests,
        "detail_requests": batch.detail_requests,
    }
    if batch.status == "failed":
        return TopicDiscoveryResult(
            status="skipped" if batch.error in ("window_closed", "disabled") else "error",
            summary={**summary, "reason": batch.error},
        )
    return TopicDiscoveryResult(status="ok" if batch.status == "ready" else "waiting", summary=summary)


async def discovery_status(scope: MemoryScope, activity_date: date) -> TopicDiscoveryResult:
    async with SESSION_LOCAL() as db:
        batch = await load_batch(db, scope, activity_date)
        if batch is None:
            return TopicDiscoveryResult(status="skipped", summary={"reason": "no_batch"})
        return await _batch_result(db, scope, batch)


async def prepare_discovery(
    scope: MemoryScope, target_date: date, timezone: ZoneInfo, context: dict[str, Any]
) -> TopicDiscoveryResult:
    activity_date = target_date + timedelta(days=1)
    async with SESSION_LOCAL() as db:
        await lock_account(db, scope.user_id)
        batch = await load_batch(db, scope, activity_date)
        if batch:
            return await _batch_result(db, scope, batch)
        await _configured(db, scope)
        await require_nightly_window(timezone)
        connections = await load_social_credentials(db, scope.user_id)
        if not connections:
            return TopicDiscoveryResult(status="skipped", summary={"reason": "unconfigured"})
        start = datetime.combine(activity_date, time.min, timezone).astimezone(UTC)
        expires = datetime.combine(activity_date + timedelta(days=1), time.min, timezone).astimezone(UTC)
        batch = await begin_batch(
            db, scope, activity_date, available_from=start, expires_at=expires, started_at=utc_now()
        )
        recent = await recent_topic_feedback(db, scope, since=batch.started_at - timedelta(days=30), limit=None)
        checkpoint = _Checkpoint(
            context={**context, "recent_topics": recent},
            source_revisions={key: value.revision for key, value in connections.items()},
        )
        await save_checkpoint(db, batch.id, scope, checkpoint.model_dump(mode="json"))
        await db.commit()
    return TopicDiscoveryResult(status="waiting", summary={"batch_id": batch.id})


def _collection_summary(checkpoint: _Checkpoint | None, diagnostics: list[LlmAttemptDiagnostic]) -> dict[str, Any]:
    summary: dict[str, Any] = {"diagnostics": [attempt.model_dump() for attempt in diagnostics]}
    if checkpoint is not None:
        summary.update(
            candidates=checkpoint.candidate_count,
            eligible=checkpoint.eligible_count,
            platforms=list(checkpoint.source_revisions),
            platform_errors=checkpoint.errors,
            platform_search_successes=checkpoint.platform_search_successes,
            duplicate_candidates=checkpoint.duplicate_candidates,
            outside_window_candidates=checkpoint.outside_window_candidates,
        )
    return summary


async def collect_topics(scope: MemoryScope, activity_date: date) -> None:
    async with SESSION_LOCAL() as db:
        batch = await load_batch(db, scope, activity_date)
    if batch is None or batch.status != "collecting":
        return
    diagnostics: list[LlmAttemptDiagnostic] = []
    checkpoint = None
    reason = None
    try:
        checkpoint = _Checkpoint.model_validate(batch.checkpoint)
        timezone = ZoneInfo(checkpoint.context["user_timezone"])
        language = checkpoint.context["language"]
        before_call = partial(_before_batch_call, scope, timezone, batch.expires_at)
        await before_call()
        async with SESSION_LOCAL() as db:
            connections = await load_social_credentials(db, scope.user_id)
        config = await load_user_llm_config(scope.user_id, AiTaskKey.nightly)
        if not checkpoint.query_plan_done:
            platforms = tuple(
                get_social_platform(key).id
                for key in checkpoint.source_revisions
                if key in connections and connections[key].revision == checkpoint.source_revisions[key]
            )
            if platforms:
                instructions = resolve_prompt_text(TOPIC_SEARCH_TEXTS, language)
                schema = _query_schema(platforms)
                payload = {
                    **checkpoint.context,
                    "available_platforms": list(platforms),
                    "window_start": batch.window_start.isoformat(),
                    "window_end": batch.window_end.isoformat(),
                }
                room = material_budget(config.chain, instructions, {**payload, "recent_topics": []}, schema)
                plan = await call_llm_json(
                    config,
                    instructions,
                    {**payload, "recent_topics": _recent_topics(checkpoint.context.get("recent_topics", []), room)},
                    schema=schema,
                    schema_name="topic_search_plan",
                    parse_output=partial(_parse_queries, platforms=platforms),
                    repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
                    reasoning_effort=NIGHTLY_REASONING_EFFORT,
                    before_call=before_call,
                    diagnostics=diagnostics,
                )
                checkpoint.queries = [_SearchRequest(**item.model_dump()) for item in plan.queries]
            checkpoint.query_plan_done = True
            await _save(scope, batch.id, checkpoint)
        await _collect(scope, batch, timezone, checkpoint, connections, config, before_call, diagnostics)
        if (
            checkpoint.queries
            and not checkpoint.search_successes
            and any(err["reason"] != "configuration_changed" for err in checkpoint.errors)
        ):
            raise SocialSourceError("All topic searches failed")
    except NightlyWindowClosedError:
        reason = "window_closed"
    except DiscoveryDisabledError:
        reason = "disabled"
    except Exception as exc:
        async with SESSION_LOCAL() as db:
            await finish_failed_batch(
                db, batch.id, scope, type(exc).__name__, _collection_summary(checkpoint, diagnostics)
            )
            await db.commit()
        raise
    summary = _collection_summary(checkpoint, diagnostics)
    if reason:
        summary["reason"] = reason
    async with SESSION_LOCAL() as db:
        await lock_account(db, scope.user_id)
        try:
            if reason == "disabled":
                raise DiscoveryDisabledError("采集期间已关闭")
            async with db.begin_nested():
                await _configured(db, scope)
                _require_batch_lifetime(batch.expires_at)
                await complete_batch(db, batch.id, scope, summary, reason=reason)
                # 大批量写入可能跨过有效期，收口前复核；失败会一并撤回刚写入的来源。
                await _configured(db, scope)
                _require_batch_lifetime(batch.expires_at)
        except (DiscoveryDisabledError, NightlyWindowClosedError) as exc:
            reason = "disabled" if isinstance(exc, DiscoveryDisabledError) else "window_closed"
            await finish_failed_batch(db, batch.id, scope, reason, {**summary, "reason": reason})
        except ValueError as exc:
            await finish_failed_batch(db, batch.id, scope, type(exc).__name__, summary)
            await db.commit()
            raise
        await db.commit()
