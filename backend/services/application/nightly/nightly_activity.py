import asyncio
import contextlib
import json
from collections.abc import Coroutine, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    SETTINGS,
    format_day_marker,
    format_local_date_str,
    format_time_anchor,
    get_logger,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    session_scope,
)
from modules.auth import User
from modules.companion import Persona
from modules.conversation import Conversation, Message
from modules.scheduler import NightlyActivityLog
from modules.settings import get_user_setting
from prompts.nightly import NIGHTLY_REFLECTION_TEXTS, REFLECTION_REPAIR_INSTRUCTIONS
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from services.contracts import EmbeddingItem, MemoryScope, MemorySource
from services.domains.companion import load_persona_definition
from services.domains.conversation import (
    UI_ONLY_SUBTYPES,
    message_text,
    user_authored_conversation,
    validate_memory_scope,
)
from services.domains.journal import collect_moment_interactions
from services.domains.memory import (
    backfill_memory_embeddings,
    list_memories,
    read_user_profile,
    resolve_user_timezone,
    review_memories,
    upsert_slotted_memory,
)
from services.infrastructure.llm import UserLlmConfig, call_llm_once, resolve_user_llm_config

from .daily_checkpoint import run_daily_checkpoint
from .journal_nightly import project_today
from .nightly_planning import ActionExecutionResult, DateContext, load_terminal_action_results, run_nightly_planning

logger = get_logger(__name__)

# 传给 planning 阶段的 recall 行数（用作高亮）。
_PLANNING_RECALL_HIGHLIGHTS: int = 10
# 规划动作以这些状态结束时，整晚记为部分失败。
_FAILED_ACTION_STATUSES = frozenset(("failed", "interrupted", "partial"))


def _local_day_utc_bounds(day: date, tz_str: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz_str)
    local_start = datetime.combine(day, time.min, zone)
    return local_start.astimezone(UTC), (local_start + timedelta(days=1)).astimezone(UTC)


def _clean_messages(messages: Sequence[Message], tz_str: str, language: str) -> list[dict[str, str]]:
    """夜间批处理对话正文；日期分界与用户时刻按用户语言作为独立项，不写入正文。"""
    clean: list[dict[str, str]] = []
    prev_date_key: str | None = None
    last_user_at: datetime | None = None
    for msg in messages:
        text_content = message_text(msg)
        if not text_content:
            continue
        cur_date_key = format_local_date_str(msg.created_at, tz_str, language)
        if cur_date_key and cur_date_key != prev_date_key:
            marker_text = format_day_marker(msg.created_at, tz_str, language)
            if marker_text:
                clean.append({"role": "user", "content": marker_text})
            prev_date_key = cur_date_key
        clean.append({"role": msg.role, "content": text_content})
        if msg.role == "user":
            clock = format_time_anchor(msg.created_at, last_user_at, tz_str, language)
            if clock:
                clean.append({"role": "user", "content": clock})
            last_user_at = msg.created_at
    return clean


def _persona_brief(persona: Persona | None) -> dict[str, str]:
    definition = load_persona_definition(persona)
    return {
        key: definition[key] for key in ("name", "personality", "speaking_style", "relationship") if definition.get(key)
    }


async def _stage_4_self_diary(
    llm_cfg: UserLlmConfig,
    scope: MemoryScope,
    clean_messages: list[dict[str, str]],
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    local_date_str: str,
    autonomous_actions: list[dict[str, Any]],
    moment_interactions: list[dict[str, Any]],
    persona: dict[str, str],
    language: str,
) -> bool:
    """Stage 4：自我日记——伙伴写下当天的个人反思。"""
    user_id = scope.user_id
    payload = {
        "today_conversations": clean_messages,
        "contextual_memories": contextual_memories,
        "background_memories": background_memories,
        "nightly_autonomous_actions": autonomous_actions,
        **({"moment_interactions": moment_interactions} if moment_interactions else {}),
        "local_date": local_date_str,
        "language": language,
        "max_content_chars": SETTINGS.diary_max_content_chars,
        "persona": persona,
    }
    instructions = resolve_prompt_text(NIGHTLY_REFLECTION_TEXTS, language)
    for attempt in range(2):
        raw = await call_llm_once(
            llm_cfg,
            instructions + (REFLECTION_REPAIR_INSTRUCTIONS if attempt else ""),
            payload,
            max_output_tokens=SETTINGS.nightly_diary_max_tokens,
            json_output=True,
        )
        parsed = parse_llm_json(raw)
        raw_content = parsed.get("content") if isinstance(parsed, dict) and set(parsed) == {"content"} else None
        content = raw_content.strip() if isinstance(raw_content, str) else ""
        if content and len(content) <= SETTINGS.diary_max_content_chars:
            break
        payload["validation_feedback"] = {
            "error": "Expected one object with a non-blank string content within max_content_chars",
            "received_content_chars": len(content),
        }
    else:
        logger.warning("nightly_activity: invalid reflection after repair", extra={"user_id": user_id})
        return False

    diary_context = f"diary:{local_date_str}"
    async with session_scope() as db:
        row = await upsert_slotted_memory(
            db,
            scope,
            diary_context,
            content,
            json.dumps(["diary", "self_reflection"]),
            source=MemorySource("diary", batch_id=local_date_str),
        )
        await db.commit()
    # diary 命名空间参与 recall 检索，落库后补向量（best-effort，不阻塞夜间流水线）。
    await backfill_memory_embeddings(scope, [EmbeddingItem(row.id, row.content, row.content_version)])

    logger.info(
        "nightly_activity: stage 4 completed",
        extra={"user_id": user_id, "diary": diary_context},
    )
    return True


async def _update_log(
    log_id: int,
    *,
    status: str,
    summary: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """在独立事务中更新夜间活动日志行；各阶段在各自事务里跑，不复用流水线开头的 session。payload 合并进已有内容（保留计划快照）。"""
    async with session_scope() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log is None:
            return
        log.status = status
        if summary is not None:
            log.summary = summary
        if payload is not None:
            log.payload = {**(log.payload or {}), **payload}
        await db.commit()


def _successful_action_facts(actions: dict[str, ActionExecutionResult]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for action_key, action in actions.items():
        if action.status not in ("succeeded", "partial") or not (fact := (action.fact or "").strip()):
            continue
        item: dict[str, Any] = {
            "action_key": action_key,
            "capability": action.capability,
            "status": action.status,
            "fact": fact,
        }
        for identifier in ("moment_id", "outfit_id", "scene_id"):
            if (value := getattr(action, identifier)) is not None:
                item[identifier] = value
        facts.append(item)
    return facts


async def _write_action_memory(
    scope: MemoryScope,
    local_date_str: str,
    actions: list[dict[str, Any]],
    language: str,
) -> None:
    """把精灵真正做过的事写入可检索自传记忆，供次日对话自然延续。"""
    if not actions:
        return
    prefix = "Nightly autonomous activities: " if language == "en" else "夜间自主活动："
    content = prefix + "；".join(str(item["fact"]) for item in actions)
    async with session_scope() as db:
        row = await upsert_slotted_memory(
            db,
            scope,
            f"recall:nightly_actions:{local_date_str}",
            content[: SETTINGS.memory_recall_max_content_chars],
            json.dumps(["other"]),
            source=MemorySource("reflection", batch_id=local_date_str),
        )
        await db.flush()
        embedding_item = EmbeddingItem(row.id, row.content, row.content_version)
        await db.commit()
    await backfill_memory_embeddings(scope, [embedding_item])


async def run_nightly_pipeline(scope: MemoryScope, target_date: date) -> bool:
    """为单作用域整理 target_date（用户本地日）：陪伴域额外执行自主规划与日记。每次执行（含跳过）写入一条 nightly_activity_logs 行供管理员查看。"""
    user_id = scope.user_id
    validate_memory_scope(scope)
    try:
        async with session_scope() as db:
            log = (
                await db.execute(
                    select(NightlyActivityLog).where(
                        NightlyActivityLog.user_id == user_id,
                        NightlyActivityLog.system_preset_id == scope.system_preset_id,
                        NightlyActivityLog.target_date == target_date,
                    ),
                )
            ).scalar_one_or_none()
            if log is None:
                log = NightlyActivityLog(
                    user_id=user_id,
                    target_date=target_date,
                    system_preset_id=scope.system_preset_id,
                    status="running",
                )
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
        logger.warning(
            "nightly_activity: failed to create log row",
            extra={"user_id": user_id, "error": str(exc)},
        )
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
        user = await db.get(User, user_id)
        tz_str = await resolve_user_timezone(db, user_id)
    if user is None or not user.nightly_activity_enabled:
        logger.info("nightly_activity: skipped, disabled by user policy", extra={"user_id": user_id})
        await _update_log(log_id, status="skipped", summary="夜间活动总控已关闭")
        return False
    if not tz_str:
        logger.info("nightly_activity: skipped, missing timezone", extra={"user_id": user_id})
        await _update_log(log_id, status="skipped", summary="缺少时区设置")
        return False
    try:
        utc_start, utc_end = _local_day_utc_bounds(target_date, tz_str)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        logger.warning(
            "nightly_activity: timezone resolution error",
            extra={"user_id": user_id, "error": str(exc)},
        )
        await _update_log(log_id, status="skipped", summary=f"时区解析错误: {exc}")
        return False
    async with session_scope() as db:
        llm_cfg = await resolve_user_llm_config(db, user_id)
    if not llm_cfg.is_configured:
        logger.info("nightly_activity: skipped, missing llm config", extra={"user_id": user_id})
        await _update_log(log_id, status="skipped", summary="缺少 LLM 配置")
        return False

    # 各阶段顺序执行，失败域相互隔离；每个阶段的结果汇入 stages 给末尾日志。
    stages: list[dict[str, Any]] = []
    try:
        await review_memories(scope, llm_config=llm_cfg)
        stages.append({"stage": "memory_review", "status": "ok"})
    except Exception as exc:
        logger.exception("nightly memory review failed", extra={"user_id": user_id})
        stages.append({"stage": "memory_review", "status": "error", "error": str(exc)})

    if scope.system_preset_id != "companion":
        has_errors = any(stage["status"] == "error" for stage in stages)
        await _update_log(log_id, status="failed" if has_errors else "completed", payload={"stages": stages})
        return not has_errors

    local_date_str = target_date.isoformat()
    # 当日对话与 7 天基线共用同一会话范围（本预设下用户本人的对话，含 IM），活动统计才可比较。
    companion_messages = (
        Conversation.user_id == user_id,
        Conversation.system_preset_id == scope.system_preset_id,
        Message.id > Conversation.context_after_message_id,
        user_authored_conversation(),
        Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
    )
    async with session_scope() as db:
        today_messages = (
            await db.scalars(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    *companion_messages,
                    Message.created_at >= utc_start,
                    Message.created_at < utc_end,
                    Message.role.in_(("user", "assistant")),
                )
                .order_by(Message.id.asc()),
            )
        ).all()
        user_language = resolve_language(await get_user_setting(db, user_id, "language"))
        # 片刻互动（当日发布 + 当日评论）作为规划、反思日记与日记投影的共享输入。
        moments = await collect_moment_interactions(db, user_id, utc_start=utc_start, utc_end=utc_end)
        past_7_count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    *companion_messages,
                    Message.role == "user",
                    Message.created_at >= utc_start - timedelta(days=7),
                    Message.created_at < utc_start,
                ),
            )
        ).scalar_one()
        # 维护完成后读取有效事实，规划与日记不使用维护前的过期快照。
        recall_rows = await list_memories(db, scope, kind="recall", limit=None)
        user_profile = await read_user_profile(db, scope)
        persona = _persona_brief(await db.scalar(select(Persona).where(Persona.user_id == user_id)))

    clean_messages = _clean_messages(today_messages, tz_str, user_language)
    today_msg_count = sum(1 for message in today_messages if message.role == "user")
    tomorrow = target_date + timedelta(days=1)
    date_context = DateContext(
        source_date=local_date_str,
        tomorrow_date=tomorrow.isoformat(),
        tomorrow_weekday=tomorrow.strftime("%A"),
        next_7_days=[(target_date + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
        user_timezone=tz_str,
    )
    anomaly_stats = {
        "today_msg_count": today_msg_count,
        "seven_day_avg": round(past_7_count / 7.0, 2),
    }
    contextual_memories = {
        str(r["id"]): f"[{r['basis']}] {r['content']}" for r in recall_rows if r["usage"] == "contextual"
    }
    background_memories = {str(r["id"]): r["content"] for r in recall_rows if r["usage"] == "background"}

    action_results: dict[str, ActionExecutionResult] = {}
    try:
        planning_result = await run_nightly_planning(
            llm_cfg,
            user_id,
            contextual_memories,
            background_memories,
            user_profile,
            recall_rows[:_PLANNING_RECALL_HIGHLIGHTS],
            date_context,
            anomaly_stats,
            clean_messages,
            moments.threads,
            log_id=log_id,
        )
        stages.append({"stage": "planning", "status": "ok", "actions": planning_result.model_dump()})
        action_results = planning_result.actions
    except Exception as exc:
        logger.exception(
            "nightly_activity: stage 3 planning failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        stages.append({"stage": "planning", "status": "error", "error": str(exc)})
        # 规划阶段中途失败时，账本里已落终态的动作仍是真实发生的事。
        try:
            action_results = await load_terminal_action_results(log_id)
        except Exception:
            logger.exception("nightly_activity: action ledger unavailable", extra={"user_id": user_id})
    action_facts = _successful_action_facts(action_results)
    try:
        await _write_action_memory(scope, local_date_str, action_facts, user_language)
    except Exception as exc:
        logger.exception(
            "nightly_activity: autonomous action memory failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        stages.append({"stage": "action memory", "status": "error", "error": str(exc)})

    # 没有可读的用户发言、成功行动或片刻互动时不虚构日记；反思与用户可见日记共用这一门控。
    has_readable_messages = any(message.role == "user" and message_text(message) for message in today_messages)
    has_material = bool(has_readable_messages or action_facts or moments.threads)
    if has_material:
        try:
            diary_ok = await _stage_4_self_diary(
                llm_cfg,
                scope,
                clean_messages,
                contextual_memories,
                background_memories,
                local_date_str,
                action_facts,
                moments.threads,
                persona,
                user_language,
            )
            stages.append({"stage": "diary", "status": "ok" if diary_ok else "error"})
        except Exception as exc:
            logger.exception(
                "nightly_activity: stage 4 diary failed",
                extra={"user_id": user_id, "error": str(exc)},
            )
            stages.append({"stage": "diary", "status": "error", "error": str(exc)})
    else:
        stages.append({"stage": "diary", "status": "skipped", "reason": "当日无互动或自主行动"})

    # Daily checkpoint 与生活空间日记投影相互独立，并发执行以缩短每用户的夜间墙钟时间。
    labels = ["daily checkpoint"]
    jobs: list[Coroutine[Any, Any, bool | None]] = [
        run_daily_checkpoint(llm_cfg, user_id, utc_start, utc_end, local_date_str, user_language),
    ]
    if has_material:
        labels.append("journal nightly")
        jobs.append(
            project_today(
                user_id,
                target_date,
                messages=clean_messages,
                llm_cfg=llm_cfg,
                nightly_actions=action_facts,
                moments=moments,
                persona=persona,
                language=user_language,
            ),
        )
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for label, result in zip(labels, results, strict=True):
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error(f"nightly_activity: {label} failed", exc_info=result, extra={"user_id": user_id})
            stages.append({"stage": label, "status": "error", "error": str(result)})
        elif result is None:
            # 两项都返回 True 已写入 / False 无需写入 / None 应写入却没有得到有效结果，失败原因已在各自内部记录。
            error = "empty compose result" if label == "journal nightly" else "summary not generated"
            logger.error(f"nightly_activity: {label} failed", extra={"user_id": user_id, "error": error})
            stages.append({"stage": label, "status": "error", "error": error})
        elif result is False:
            stages.append({"stage": label, "status": "skipped"})
        else:
            stages.append({"stage": label, "status": "ok"})
    if not has_material:
        stages.append({"stage": "journal nightly", "status": "skipped"})

    has_errors = any(stage["status"] == "error" for stage in stages) or any(
        action.status in _FAILED_ACTION_STATUSES for action in action_results.values()
    )
    await _update_log(
        log_id,
        status="completed_with_errors" if has_errors else "completed",
        summary="; ".join(f"{s['stage']}:{s['status']}" for s in stages),
        payload={"stages": stages, "target_date": local_date_str},
    )
    return True
