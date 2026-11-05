import contextlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from features.accounts.queries import nightly_account
from features.companion.narrative_quality import NarrativeQualityError
from features.companion.planning_queries import PersonaPlanningSnapshot, planning_persona
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting
from features.conversation.evidence import OriginalMessageSnapshot, nightly_messages, nightly_user_message_count
from features.life.context import read_life_context_in_session
from features.memory.memory_admin import list_memories
from features.memory.memory_bootstrap import read_user_profile
from features.memory.memory_format import format_memory_record
from features.memory.memory_narratives import load_companion_reflection, save_companion_reflection
from features.memory.memory_review import review_memories
from features.memory.memory_store import schedule_embedding_rebuild
from features.posts.store import collect_post_interactions
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.text import format_local_iso, resolve_language, resolve_prompt_text, utc_now
from runtime.memory_scope import MemoryScope, validate_memory_scope
from runtime.presets import COMPANION_PRESET_ID
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from workflows.nightly.journal_nightly import project_today
from workflows.nightly.models import NightlyActivityLog
from workflows.nightly.nightly_planning import (
    ActionExecutionResult,
    DateContext,
    load_action_results,
    run_nightly_planning,
)
from workflows.nightly.output_contracts import parse_reflection, reflection_schema
from workflows.nightly.prompts import NIGHTLY_JSON_REPAIR_TEXTS, NIGHTLY_REFLECTION_TEXTS
from workflows.nightly.stage_state import load_narrative_result, save_narrative_result
from workflows.nightly.topic_discovery import DiscoveryDisabledError, discover_topics
from workflows.nightly.window import NightlyWindowClosedError, in_nightly_window, require_nightly_window

logger = get_logger(__name__)

# 传给 planning 阶段的 recall 行数（用作高亮）。
_PLANNING_RECALL_HIGHLIGHTS: int = 10
# 规划动作以这些状态结束时，整晚记为部分失败。
_FAILED_ACTION_STATUSES = frozenset(("failed", "interrupted", "result_unknown"))


def _record_stage(stages: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    """更新同一目标日的阶段结论，保留恢复诊断；跳过不覆盖已有实际结论。"""
    existing = next((stage for stage in stages if stage["stage"] == entry["stage"]), None)
    if entry["status"] == "skipped" and existing is not None and existing["status"] != "skipped":
        return
    if existing is not None and "diagnostics" in existing:
        entry = {"diagnostics": existing["diagnostics"], **entry}
    stages[:] = [stage for stage in stages if stage["stage"] != entry["stage"]]
    stages.append(entry)


async def _run_stage[T](
    stages: list[dict[str, Any]],
    name: str,
    run: Callable[[], Awaitable[T]],
    describe: Callable[[T], tuple[str, dict[str, Any]]],
    *,
    user_id: int,
    skipped: tuple[type[BaseException], ...] = (NightlyWindowClosedError,),
    error_note: Callable[[Exception], dict[str, Any]] | None = None,
) -> T | None:
    """执行一个阶段并记录真实结论；业务异常只影响本阶段。"""
    try:
        value = await run()
    except skipped as exc:
        _record_stage(stages, {"stage": name, "status": "skipped", "reason": str(exc)})
        return None
    except Exception as exc:
        logger.exception("nightly stage failed", extra={"stage": name, "user_id": user_id})
        note = error_note(exc) if error_note is not None else {"error": str(exc)}
        _record_stage(stages, {"stage": name, "status": "error", **note})
        return None
    status, extra = describe(value)
    _record_stage(stages, {"stage": name, "status": status, **extra})
    return value


def _local_day_utc_bounds(day: date, tz_str: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz_str)
    local_start = datetime.combine(day, time.min, zone)
    return local_start.astimezone(UTC), (local_start + timedelta(days=1)).astimezone(UTC)


def _clean_messages(messages: Sequence[OriginalMessageSnapshot], tz_str: str) -> list[dict[str, str]]:
    return [
        {
            "role": msg.role,
            "content": content,
            "created_at": format_local_iso(msg.created_at, tz_str) or msg.created_at.isoformat(),
            "message_id": str(msg.id),
            "session_id": str(msg.conversation_id),
        }
        for msg in messages
        if (content := msg.content)
    ]


def _persona_brief(persona: PersonaPlanningSnapshot | None) -> dict[str, str]:
    definition = persona.definition if persona is not None else {}
    return {
        key: definition[key] for key in ("name", "personality", "speaking_style", "relationship") if definition.get(key)
    }


async def _stage_4_reflection(
    scope: MemoryScope,
    clean_messages: list[dict[str, str]],
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    local_date_str: str,
    autonomous_actions: list[dict[str, Any]],
    post_interactions: list[dict[str, Any]],
    life_context: dict[str, Any],
    persona: dict[str, str],
    language: str,
    log_id: int,
    *,
    user_timezone: str,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> bool:
    async with session_scope() as db:
        previous_result = await load_narrative_result(db, log_id, "reflection")
        if previous_result is not None:
            return previous_result
        previous = await load_companion_reflection(db, scope)
        previous_date = previous.local_date if previous is not None else None
        if previous_date is not None and previous_date >= local_date_str:
            await save_narrative_result(db, log_id, "reflection", True)
            await db.commit()
            return True
        previous_reflection = (
            {"local_date": previous_date, "content": previous.content} if previous is not None else None
        )
    payload = {
        "today_conversations": clean_messages,
        "contextual_memories": contextual_memories,
        "background_memories": background_memories,
        "previous_reflection": previous_reflection,
        "nightly_autonomous_actions": autonomous_actions,
        "post_interactions": post_interactions,
        "life_context": life_context,
        "local_date": local_date_str,
        "language": language,
        "max_content_chars": SETTINGS.reflection_max_content_chars,
        "persona": persona,
    }
    max_content_chars = SETTINGS.reflection_max_content_chars
    content = await call_llm_json(
        await load_user_llm_config(scope.user_id, AiTaskKey.nightly),
        resolve_prompt_text(NIGHTLY_REFLECTION_TEXTS, language),
        payload,
        schema=reflection_schema(max_content_chars),
        schema_name="nightly_reflection",
        parse_output=partial(parse_reflection, max_content_chars=max_content_chars),
        repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
        reasoning_effort=NIGHTLY_REASONING_EFFORT,
        before_call=partial(require_nightly_window, ZoneInfo(user_timezone)),
        diagnostics=diagnostics,
    )
    if content is None:
        async with session_scope() as db:
            await save_narrative_result(db, log_id, "reflection", False)
            await db.commit()
        return False
    async with session_scope() as db:
        await save_companion_reflection(db, scope, content, date.fromisoformat(local_date_str))
        await save_narrative_result(db, log_id, "reflection", True)
        await db.commit()
    schedule_embedding_rebuild(scope.user_id)
    return True


async def _update_log(
    log_id: int, *, status: str, summary: str | None = None, payload: dict[str, Any] | None = None
) -> None:
    """在独立事务中更新夜间活动日志行；各阶段在各自事务里跑，不复用流水线开头的 session。"""
    async with session_scope() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log is None:
            return
        log.status = status
        if summary is not None:
            log.summary = summary
        if payload is not None:
            log.payload = {**(log.payload or {}), **payload}
            if status == "completed" and any(item["status"] == "error" for item in payload.get("stages", [])):
                log.status = "completed_with_errors"
        await db.commit()


def _stage_results(
    stages: list[dict[str, Any]], diagnostics: dict[str, list[LlmAttemptDiagnostic]]
) -> list[dict[str, Any]]:
    return [
        {
            **stage,
            **(
                {
                    "diagnostics": [
                        *stage.get("diagnostics", []),
                        *(item.model_dump() for item in diagnostics[stage["stage"]]),
                    ]
                }
                if diagnostics.get(stage["stage"])
                else {}
            ),
        }
        for stage in stages
    ]


def _successful_action_facts(actions: dict[str, ActionExecutionResult]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for action_key, action in actions.items():
        if action.status != "succeeded" or not (fact := (action.fact or "").strip()):
            continue
        item: dict[str, Any] = {
            "action_key": action_key,
            "step_id": action.life_step_id,
            "capability": action.capability,
            "status": action.status,
            "fact": fact,
        }
        for identifier in ("outfit_id", "scene_id"):
            if (value := getattr(action, identifier)) is not None:
                item[identifier] = value
        facts.append(item)
    return facts


async def run_nightly_pipeline(scope: MemoryScope, target_date: date) -> bool:
    """整理陪伴域的一个本地日，复用当日日志和已完成阶段。"""
    if scope.system_preset_id != COMPANION_PRESET_ID:
        raise ValueError(f"Nightly pipeline only runs for the companion preset, got {scope.system_preset_id!r}")
    user_id = scope.user_id
    validate_memory_scope(scope)
    try:
        async with session_scope() as db:
            log = (
                await db.execute(
                    select(NightlyActivityLog).where(
                        NightlyActivityLog.user_id == user_id, NightlyActivityLog.target_date == target_date
                    )
                )
            ).scalar_one_or_none()
            if log is None:
                timezone = await resolve_user_timezone(db, user_id)
                if timezone is None or not in_nightly_window(utc_now(), ZoneInfo(timezone)):
                    return False
                log = NightlyActivityLog(user_id=user_id, target_date=target_date, status="running")
                db.add(log)
            elif log.status in ("completed", "completed_with_errors"):
                logger.info(
                    "nightly_activity: target date already completed",
                    extra={"user_id": user_id, "target_date": target_date.isoformat()},
                )
                return True
            else:
                log.status = "running"
                log.summary = ""
            await db.commit()
            log_id = log.id
    except SQLAlchemyError as exc:
        logger.warning("nightly_activity: failed to create log row", extra={"user_id": user_id, "error": str(exc)})
        return False

    try:
        return await _run_nightly_pipeline_inner(scope, target_date, log_id)
    except Exception as exc:
        # DB 写失败不应掩盖原始异常——cron 的 logger 已有 exc_info，这里只尽力留个 failed 行供 admin 看。
        with contextlib.suppress(Exception):
            await _update_log(log_id, status="failed", summary=f"未捕获异常: {exc}")
        raise


async def _run_nightly_pipeline_inner(scope: MemoryScope, target_date: date, log_id: int) -> bool:
    user_id = scope.user_id
    async with session_scope() as db:
        user = await nightly_account(db, user_id)
        tz_str = await resolve_user_timezone(db, user_id)
        log = await db.get(NightlyActivityLog, log_id)
        payload = (log.payload or {}) if log is not None else {}
        has_plan = "nightly_plan" in payload
        stages = list(payload.get("stages", []))
    if user is None or not user.is_active:
        logger.info("nightly_activity: skipped, inactive account", extra={"user_id": user_id})
        await _update_log(log_id, status="skipped", summary="账户不可用")
        return False
    if not tz_str:
        logger.info("nightly_activity: skipped, missing timezone", extra={"user_id": user_id})
        await _update_log(log_id, status="skipped", summary="缺少时区设置")
        return False
    try:
        utc_start, utc_end = _local_day_utc_bounds(target_date, tz_str)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        logger.warning("nightly_activity: timezone resolution error", extra={"user_id": user_id, "error": str(exc)})
        await _update_log(log_id, status="skipped", summary=f"时区解析错误: {exc}")
        return False
    # 各阶段顺序执行，失败域相互隔离；恢复时沿用已记录的阶段结论。
    stage_diagnostics: dict[str, list[LlmAttemptDiagnostic]] = {}

    if in_nightly_window(utc_now(), ZoneInfo(tz_str)):
        await _run_stage(
            stages,
            "memory_review",
            partial(
                review_memories,
                scope,
                task=AiTaskKey.nightly,
                before_call=partial(require_nightly_window, ZoneInfo(tz_str)),
                reasoning_effort=NIGHTLY_REASONING_EFFORT,
            ),
            lambda reviewed: ("ok" if reviewed else "skipped", {}),
            user_id=user_id,
        )
    else:
        _record_stage(stages, {"stage": "memory_review", "status": "skipped", "reason": "夜间窗口已结束"})

    local_date_str = target_date.isoformat()
    # 当日对话与 7 天基线共用同一会话范围（本预设下用户本人的对话），活动统计才可比较。
    async with session_scope() as db:
        today_messages = await nightly_messages(db, user_id, scope.system_preset_id, utc_start, utc_end)
        user_language = resolve_language(await get_user_setting(db, user_id, "language"))
        posts = await collect_post_interactions(
            db, user_id, local_date=target_date, user_timezone=tz_str, utc_start=utc_start, utc_end=utc_end
        )
        past_7_count = await nightly_user_message_count(
            db, user_id, scope.system_preset_id, utc_start - timedelta(days=7), utc_start
        )
        # 维护完成后读取有效事实，规划与日记不使用维护前的过期快照。
        recall_rows = await list_memories(db, scope, kind="recall", limit=None)
        user_profile = await read_user_profile(db, scope)
        persona_snapshot = await planning_persona(db, user_id)
        life_context = (await read_life_context_in_session(db, user_id)).prompt_payload()
        persona = _persona_brief(persona_snapshot)
        previous_reflection = await load_companion_reflection(db, scope)

    clean_messages = _clean_messages(today_messages, tz_str)
    today_msg_count = sum(1 for message in today_messages if message.role == "user")
    tomorrow = target_date + timedelta(days=1)
    date_context = DateContext(
        source_date=local_date_str,
        tomorrow_date=tomorrow.isoformat(),
        tomorrow_weekday=tomorrow.strftime("%A"),
        next_7_days=[(target_date + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
        user_timezone=tz_str,
    )
    anomaly_stats = {"today_msg_count": today_msg_count, "seven_day_avg": round(past_7_count / 7.0, 2)}
    contextual_memories = {
        str(r.id): format_memory_record(r.content, r.basis, r.context, language=user_language)
        for r in recall_rows
        if r.usage == "contextual"
    }
    background_memories = {
        str(r.id): format_memory_record(r.content, r.basis, r.context, language=user_language)
        for r in recall_rows
        if r.usage == "background"
    }

    topic_reserve: list[dict[str, Any]] = []
    if user.nightly_activity_enabled and not has_plan:
        topic_result = await _run_stage(
            stages,
            "topic discovery",
            partial(
                discover_topics,
                scope,
                target_date,
                ZoneInfo(tz_str),
                {
                    "persona": persona_snapshot.definition if persona_snapshot is not None else {},
                    "user_profile": user_profile,
                    "ordinary_memories": [row.model_dump() for row in recall_rows],
                    "previous_reflection": previous_reflection.content if previous_reflection is not None else None,
                    "life_context": life_context,
                    "today_conversations": clean_messages,
                    "source_date": local_date_str,
                    "activity_date": tomorrow.isoformat(),
                    "user_timezone": tz_str,
                    "language": user_language,
                },
                user_language,
                diagnostics=stage_diagnostics.setdefault("topic discovery", []),
            ),
            lambda result: (result.status, result.summary),
            user_id=user_id,
            skipped=(NightlyWindowClosedError, DiscoveryDisabledError),
            error_note=lambda exc: {"error": type(exc).__name__},
        )
        if topic_result is not None:
            topic_reserve = topic_result.topics
    elif not user.nightly_activity_enabled:
        _record_stage(stages, {"stage": "topic discovery", "status": "skipped", "reason": "夜间自主规划已关闭"})

    action_results: dict[str, ActionExecutionResult] = {}
    if user.nightly_activity_enabled or has_plan:
        planning_result = await _run_stage(
            stages,
            "planning",
            partial(
                run_nightly_planning,
                user_id,
                contextual_memories,
                background_memories,
                user_profile,
                recall_rows[:_PLANNING_RECALL_HIGHLIGHTS],
                date_context,
                anomaly_stats,
                clean_messages,
                posts.threads,
                log_id=log_id,
                persona=persona_snapshot,
                life_context=life_context,
                language=user_language,
                topic_reserve=topic_reserve,
                diagnostics=stage_diagnostics.setdefault("planning", []),
            ),
            lambda result: ("waiting" if result.pending else "ok", {}),
            user_id=user_id,
        )
        if planning_result is not None:
            action_results = planning_result.actions
            if planning_result.pending:
                await _update_log(
                    log_id,
                    status="running",
                    summary="等待已受理的生活步骤",
                    payload={"stages": _stage_results(stages, stage_diagnostics)},
                )
                return False
        else:
            # 窗口已结束或规划失败时，账本里已落终态的动作仍是真实发生的事。
            try:
                action_results = await load_action_results(log_id)
            except Exception:
                logger.exception("nightly_activity: action ledger unavailable", extra={"user_id": user_id})
    else:
        _record_stage(stages, {"stage": "planning", "status": "skipped", "reason": "夜间自主规划已关闭"})

    # 刷新本轮新发布的动态，并在下面排除重复的动作事实。
    async with session_scope() as db:
        posts = await collect_post_interactions(
            db, user_id, local_date=target_date, user_timezone=tz_str, utc_start=utc_start, utc_end=utc_end
        )
    action_facts = _successful_action_facts(action_results)
    # 没有可读的用户发言、成功行动或动态互动时不虚构日记；反思与用户可见日记共用这一门控。
    has_readable_messages = any(message.role == "user" and message.content for message in today_messages)
    has_material = bool(has_readable_messages or action_facts or posts.threads)
    if has_material:
        await _run_stage(
            stages,
            "reflection",
            partial(
                _stage_4_reflection,
                scope,
                clean_messages,
                contextual_memories,
                background_memories,
                local_date_str,
                action_facts,
                posts.threads,
                life_context,
                persona,
                user_language,
                log_id,
                user_timezone=tz_str,
                diagnostics=stage_diagnostics.setdefault("reflection", []),
            ),
            lambda ok: ("ok" if ok else "skipped", {}),
            user_id=user_id,
        )
    else:
        async with session_scope() as db:
            for stage in ("reflection", "journal"):
                if await load_narrative_result(db, log_id, stage) is None:
                    await save_narrative_result(db, log_id, stage, False)
            await db.commit()
        _record_stage(stages, {"stage": "reflection", "status": "skipped", "reason": "当日无互动或自主行动"})

    if has_material:
        await _run_stage(
            stages,
            "journal nightly",
            partial(
                project_today,
                user_id,
                target_date,
                log_id=log_id,
                contextual_memories=contextual_memories,
                background_memories=background_memories,
                user_timezone=tz_str,
                messages=clean_messages,
                nightly_actions=action_facts,
                posts=posts,
                persona=persona,
                language=user_language,
                diagnostics=stage_diagnostics.setdefault("journal nightly", []),
            ),
            lambda ok: ("ok" if ok else "skipped", {}),
            user_id=user_id,
            skipped=(NightlyWindowClosedError, NarrativeQualityError),
        )
    else:
        _record_stage(stages, {"stage": "journal nightly", "status": "skipped"})

    has_errors = any(stage["status"] == "error" for stage in stages) or any(
        action.status in _FAILED_ACTION_STATUSES for action in action_results.values()
    )
    await _update_log(
        log_id,
        status="completed_with_errors" if has_errors else "completed",
        summary="; ".join(f"{s['stage']}:{s['status']}" for s in stages),
        payload={"stages": _stage_results(stages, stage_diagnostics), "target_date": local_date_str},
    )
    return True
