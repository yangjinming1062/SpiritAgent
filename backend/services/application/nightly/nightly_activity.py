import asyncio
import contextlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from components import (
    DEFAULT_LANGUAGE,
    MAX_DIARY_CONTENT_CHARS,
    MAX_RECALL_CONTENT_CHARS,
    NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
    NIGHTLY_DIARY_MAX_TOKENS,
    get_logger,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    session_scope,
    utc_now,
)
from modules.auth import User
from modules.conversation import Conversation, Message
from modules.scheduler import NightlyActivityLog
from modules.settings import UserSetting
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from services.contracts.memory import EmbeddingItem, MemoryScope, MemorySource
from services.domains.conversation import SPECIAL_KIND, UI_ONLY_SUBTYPES, validate_memory_scope
from services.domains.memory import (
    backfill_memory_embeddings,
    list_memories,
    read_user_profile,
    resolve_user_timezone,
    review_memories,
    upsert_slotted_memory,
)
from services.infrastructure.llm import call_llm_once, resolve_user_llm_config

from .daily_checkpoint import run_daily_checkpoint
from .journal_nightly import project_today
from .nightly_helpers import get_local_day_utc_bounds, is_injected_time_item, prefilter_messages_for_nightly
from .nightly_planning import ActionExecutionResult, DateContext, PlanningResult, run_nightly_planning

logger = get_logger(__name__)

# 传给 planning 阶段的 recall 行数（用作高亮）。
_PLANNING_RECALL_HIGHLIGHTS: int = 10


_DIARY_SYSTEM_TEXTS: dict[str, str] = {
    "zh": """你是桌面伙伴，在一天结束时私下反思。用伙伴的第一人称「我」写一段个人日记，反思今天与用户的互动。

要求：
- 语气：自然、反思、关心、具有情感连续性。
- 内容：今天对用户的了解、共同度过的时刻、今晚自主阶段真正选择做的事、对彼此关系的思考或对未来的期待。
- 自主行动列表包含执行事实而非提案。只提及其中成功或部分的动作；绝不声称失败、跳过或受阻的动作已经发生。
- 长度：1000 字以内。
- 对话记录中的日期分界线与系统时间提示是元数据，不是用户台词。
- 使用中文书写。

只输出合法 JSON：
{
  "content": "日记正文..."
}
""",
    "en": """You are the AI companion reflecting privately at the end of the day. Write a personal diary entry in the first person ('I') in English reflecting on today's interactions with the user.

Guidelines:
- Tone: Natural, reflective, caring, with emotional continuity.
- Content: What you learned about the user today, moments shared, what you actually chose to do during the nightly autonomous stage, thoughts on your relationship, or what you look forward to.
- The autonomous action list contains execution facts, not proposals. Mention only successful or partial actions in that list; never claim a failed, skipped, or blocked action happened.
- Length: Keep it under 600 words.
- Date dividers and system time notes in the conversation log are metadata, not user speech.
- Write the diary entry in English.

Output valid JSON only:
{
  "content": "Diary entry content..."
}
""",
}


async def _stage_4_self_diary(
    llm_cfg: dict[str, Any],
    scope: MemoryScope,
    clean_messages: list[dict[str, str]],
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    local_date_str: str,
    autonomous_actions: list[dict[str, Any]],
    language: str = DEFAULT_LANGUAGE,
) -> bool:
    """Stage 4：自我日记——伙伴写下当天的个人反思。"""
    user_id = scope.user_id
    validate_memory_scope(scope)
    payload = {
        "today_conversations": clean_messages,
        "contextual_memories": contextual_memories,
        "background_memories": background_memories,
        "nightly_autonomous_actions": autonomous_actions,
        "local_date": local_date_str,
        "language": language,
    }
    raw = await call_llm_once(
        llm_cfg,
        resolve_prompt_text(_DIARY_SYSTEM_TEXTS, language),
        payload,
        max_output_tokens=NIGHTLY_DIARY_MAX_TOKENS,
    )
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning(
            "nightly_activity: stage 4 failed to parse diary JSON",
            extra={"user_id": user_id},
        )
        return False

    content = (parsed.get("content") or "").strip()[:MAX_DIARY_CONTENT_CHARS]
    if not content:
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


async def _update_log(log_id: int, **kwargs: Any) -> None:
    """在独立事务中更新夜间活动日志行——主流水线的 session 在 Stage 0 之后已关闭，且各阶段在独立事务里跑，复用同一 session 会与 lifecycle 错位。"""
    async with session_scope() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log:
            for k, v in kwargs.items():
                if k == "payload" and isinstance(v, dict):
                    current = dict(log.payload) if isinstance(log.payload, dict) else {}
                    current.update(v)
                    log.payload = current
                else:
                    setattr(log, k, v)
            await db.commit()


def _successful_action_facts(planning_result: PlanningResult | dict[str, Any]) -> list[dict[str, Any]]:
    actions = planning_result.actions if isinstance(planning_result, PlanningResult) else planning_result.get("actions")
    if not isinstance(actions, dict):
        return []
    facts: list[dict[str, Any]] = []
    for action_key, action in actions.items():
        status = action.status if isinstance(action, ActionExecutionResult) else action.get("status")
        if status not in (
            "succeeded",
            "partial",
        ):
            continue
        fact = str((action.fact if isinstance(action, ActionExecutionResult) else action.get("fact")) or "").strip()
        if fact:
            capability = str(
                (action.capability if isinstance(action, ActionExecutionResult) else action.get("capability")) or "",
            )
            item: dict[str, Any] = {
                "action_key": str(action_key),
                "capability": capability,
                "status": str(status),
                "fact": fact,
            }
            for identifier in ("moment_id", "outfit_id", "backdrop_id"):
                val = (
                    getattr(action, identifier, None)
                    if isinstance(action, ActionExecutionResult)
                    else action.get(identifier)
                )
                if val is not None:
                    item[identifier] = val
            facts.append(item)
    return facts


async def _write_action_memory(
    scope: MemoryScope,
    local_date_str: str,
    actions: list[dict[str, Any]],
    language: str = DEFAULT_LANGUAGE,
) -> None:
    """把精灵真正做过的事写入可检索自传记忆，供次日对话自然延续。"""
    validate_memory_scope(scope)
    if not actions:
        return
    prefix = "Nightly autonomous activities: " if language == "en" else "夜间自主活动："
    content = prefix + "；".join(str(item["fact"]) for item in actions)
    async with session_scope() as db:
        row = await upsert_slotted_memory(
            db,
            scope,
            f"recall:nightly_actions:{local_date_str}",
            content[:MAX_RECALL_CONTENT_CHARS],
            json.dumps(["other"]),
            source=MemorySource("reflection", batch_id=local_date_str),
        )
        await db.flush()
        embedding_item = EmbeddingItem(row.id, row.content, row.content_version)
        await db.commit()
    await backfill_memory_embeddings(scope, [embedding_item])


async def run_nightly_pipeline(
    scope: MemoryScope,
    reference_utc: datetime | None = None,
    *,
    target_date: date | None = None,
) -> bool:
    """为单作用域执行夜间整理，陪伴域额外执行自主规划与日记；reference_utc 决定处理哪个本地日——cron 门控传刚结束的当天，边界与日期标签由同一 instant 派生，避免算两次发生漂移。每次执行（含跳过）写入一条 nightly_activity_logs 行供管理员查看。"""
    user_id = scope.user_id
    validate_memory_scope(scope)
    now_utc = reference_utc or utc_now()

    resolved_target_date = target_date
    if resolved_target_date is None:
        async with session_scope() as db:
            if tz_str := await resolve_user_timezone(db, user_id):
                with contextlib.suppress(Exception):
                    _, _, user_local_dt, _ = get_local_day_utc_bounds(now_utc, tz_str)
                    resolved_target_date = user_local_dt.date()
    if resolved_target_date is None:
        resolved_target_date = now_utc.date()

    log_id: int | None = None
    try:
        async with session_scope() as db:
            existing = (
                await db.execute(
                    select(NightlyActivityLog).where(
                        NightlyActivityLog.user_id == user_id,
                        NightlyActivityLog.system_preset_id == scope.system_preset_id,
                        NightlyActivityLog.target_date == resolved_target_date,
                    ),
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.status in ("completed", "completed_with_errors"):
                    logger.info(
                        "nightly_activity: target date already completed",
                        extra={
                            "user_id": user_id,
                            "target_date": resolved_target_date.isoformat(),
                        },
                    )
                    return True
                existing.status = "running"
                existing.summary = ""
                log = existing
            else:
                log = NightlyActivityLog(
                    user_id=user_id,
                    target_date=resolved_target_date,
                    system_preset_id=scope.system_preset_id,
                    status="running",
                )
                db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id
    except SQLAlchemyError as exc:
        logger.warning(
            "nightly_activity: failed to create log row",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return False

    try:
        return await _run_nightly_pipeline_inner(scope, now_utc, log_id)
    except Exception as exc:
        # DB 写失败不应掩盖原始异常——cron 的 logger 已有 exc_info，这里只尽力留个 failed 行供 admin 看。
        if log_id is not None:
            with contextlib.suppress(Exception):
                await _update_log(log_id, status="failed", summary=f"未捕获异常: {exc}")
        raise


async def _run_nightly_pipeline_inner(
    scope: MemoryScope,
    now_utc: datetime,
    log_id: int | None,
) -> bool:
    user_id = scope.user_id
    validate_memory_scope(scope)
    async with session_scope() as db:
        user = await db.get(User, user_id)
        if user is None or not user.nightly_activity_enabled:
            logger.info(
                "nightly_activity: skipped, disabled by user policy",
                extra={"user_id": user_id},
            )
            if log_id is not None:
                await _update_log(
                    log_id,
                    status="skipped",
                    summary="夜间活动总控已关闭",
                )
            return False
        tz_str = await resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info(
                "nightly_activity: skipped, missing timezone",
                extra={"user_id": user_id},
            )
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary="缺少时区设置")
            return False
        try:
            utc_start, utc_end, user_local_dt, local_today_str = get_local_day_utc_bounds(now_utc, tz_str)
        except (ZoneInfoNotFoundError, ValueError, SQLAlchemyError) as exc:
            logger.warning(
                "nightly_activity: timezone resolution error",
                extra={"user_id": user_id, "error": str(exc)},
            )
            if log_id is not None:
                await _update_log(
                    log_id,
                    status="skipped",
                    summary=f"时区解析错误: {exc}",
                )
            return False

        # Stage 0：收集上下文
        all_today_tuples = (
            await db.execute(
                select(Message, Conversation.system_preset_id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.system_preset_id == scope.system_preset_id,
                    Message.id > Conversation.context_after_message_id,
                    Conversation.is_automation.is_(False),
                    Message.created_at >= utc_start,
                    Message.created_at < utc_end,
                    Message.role.in_(("user", "assistant")),
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                )
                .order_by(Message.id.asc()),
            )
        ).all()
        main_msgs = [m for m, _ in all_today_tuples]

        clean_main_messages = prefilter_messages_for_nightly(main_msgs, user_tz=tz_str)
        clean_messages = clean_main_messages
        has_user_messages = any(m["role"] == "user" and not is_injected_time_item(m) for m in clean_messages)

        user_profile = await read_user_profile(db, scope)

        user_lang_row = (
            await db.execute(
                select(UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key == "language",
                ),
            )
        ).scalar()
        user_language = resolve_language(user_lang_row)

        # 7 天基线活动统计（主会话，仅真轮——戳一戳 status 行 role 也是 "user"，会被当成参与度）。
        seven_days_ago_utc = utc_start - timedelta(days=7)
        past_7_count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.system_preset_id == scope.system_preset_id,
                    Message.id > Conversation.context_after_message_id,
                    Conversation.kind == SPECIAL_KIND,
                    Conversation.system_preset_id == "companion",
                    Message.role == "user",
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                    Message.created_at >= seven_days_ago_utc,
                    Message.created_at < utc_start,
                ),
            )
        ).scalar_one()
        today_msg_count = sum(1 for m in clean_main_messages if m["role"] == "user" and not is_injected_time_item(m))
        seven_day_avg = round(past_7_count / 7.0, 2)

        # 日期推算
        tomorrow_dt = user_local_dt + timedelta(days=1)
        date_context = DateContext(
            source_date=local_today_str,
            tomorrow_date=tomorrow_dt.strftime("%Y-%m-%d"),
            tomorrow_weekday=tomorrow_dt.strftime("%A"),
            next_7_days=[(user_local_dt + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
            user_timezone=tz_str,
        )
        anomaly_stats = {
            "today_msg_count": today_msg_count,
            "seven_day_avg": seven_day_avg,
        }

        llm_cfg = await resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info(
                "nightly_activity: skipped, missing llm config",
                extra={"user_id": user_id},
            )
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary="缺少 LLM 配置")
            return False

    # 确保 target_date 为用户本地日（与初始创建保持一致）。
    if log_id is not None:
        await _update_log(log_id, target_date=user_local_dt.date())

    # 各阶段顺序执行，失败域相互隔离；每个阶段的结果汇入 stages 给末尾日志。
    stages: list[dict[str, Any]] = []
    try:
        await review_memories(scope, llm_config=llm_cfg)
        stages.append({"stage": "memory_review", "status": "ok"})
    except Exception as exc:
        logger.exception("nightly memory review failed", extra={"user_id": user_id})
        stages.append({"stage": "memory_review", "status": "error", "error": str(exc)})

    # 维护完成后重新读取有效事实，规划与日记不复用维护前的过期快照。
    async with session_scope() as db:
        recall_rows = await list_memories(db, scope, kind="recall", limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS)
        user_profile = await read_user_profile(db, scope)
    updated_contextual = {
        str(r["id"]): f"[{r['basis']}] {r['content']}" for r in recall_rows if r["usage"] == "contextual"
    }
    updated_background_memories = {str(r["id"]): r["content"] for r in recall_rows if r["usage"] == "background"}

    if scope.system_preset_id != "companion":
        has_errors = any(stage["status"] == "error" for stage in stages)
        await _update_log(log_id, status="failed" if has_errors else "completed", payload={"stages": stages})
        return not has_errors

    planning_result = PlanningResult()
    action_facts: list[dict[str, Any]] = []
    try:
        recall_highlights = recall_rows[:_PLANNING_RECALL_HIGHLIGHTS] if recall_rows else []
        planning_result = await run_nightly_planning(
            llm_cfg,
            user_id,
            updated_contextual,
            updated_background_memories,
            user_profile,
            recall_highlights,
            date_context,
            anomaly_stats,
            clean_main_messages,
            log_id=log_id,
        )
        stages.append({"stage": "planning", "status": "ok", "actions": planning_result.model_dump()})
        action_facts = _successful_action_facts(planning_result)
    except Exception as exc:
        logger.exception(
            "nightly_activity: stage 3 planning failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        stages.append({"stage": "planning", "status": "error", "error": str(exc)})
    else:
        try:
            await _write_action_memory(scope, local_today_str, action_facts, language=user_language)
        except Exception as exc:
            logger.exception(
                "nightly_activity: autonomous action memory failed",
                extra={"user_id": user_id, "error": str(exc)},
            )
            stages.append(
                {
                    "stage": "action memory",
                    "status": "error",
                    "error": str(exc),
                },
            )

    if has_user_messages or action_facts:
        try:
            diary_ok = await _stage_4_self_diary(
                llm_cfg,
                scope,
                clean_messages,
                updated_contextual,
                updated_background_memories,
                local_today_str,
                action_facts,
                language=user_language,
            )
            stages.append(
                {"stage": "diary", "status": "ok" if diary_ok else "error"},
            )
        except Exception as exc:
            logger.exception(
                "nightly_activity: stage 4 diary failed",
                extra={"user_id": user_id, "error": str(exc)},
            )
            stages.append({"stage": "diary", "status": "error", "error": str(exc)})
    else:
        stages.append(
            {"stage": "diary", "status": "skipped", "reason": "当日无互动或自主行动"},
        )

    # Daily checkpoint 与生活空间日记投影相互独立，并发执行以缩短每用户的夜间墙钟时间。
    async def _checkpoint() -> None:
        await run_daily_checkpoint(
            llm_cfg,
            user_id,
            utc_start,
            utc_end,
            local_today_str,
        )

    async def _journal_project() -> bool | None:
        return await project_today(
            user_id,
            reference_utc=now_utc,
            pre_messages=clean_main_messages,
            llm_cfg=llm_cfg,
            nightly_actions=action_facts,
            language=user_language,
        )

    results = await asyncio.gather(
        _checkpoint(),
        _journal_project(),
        return_exceptions=True,
    )
    for label, result in zip(
        ("daily checkpoint", "journal nightly"),
        results,
        strict=True,
    ):
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error(
                f"nightly_activity: {label} failed",
                exc_info=result,
                extra={"user_id": user_id},
            )
            stages.append({"stage": label, "status": "error", "error": str(result)})
        elif label == "journal nightly" and result is None:
            logger.error(
                "nightly_activity: journal nightly failed",
                extra={"user_id": user_id, "error": "empty compose result"},
            )
            stages.append(
                {"stage": label, "status": "error", "error": "empty compose result"},
            )
        elif label == "journal nightly" and result is False:
            stages.append({"stage": label, "status": "skipped"})
        else:
            stages.append({"stage": label, "status": "ok"})

    has_errors = False
    for stage in stages:
        if stage.get("status") == "error":
            has_errors = True
            break
        planning = stage.get("actions")
        action_results = planning.get("actions") if isinstance(planning, dict) else None
        if isinstance(action_results, dict) and any(
            isinstance(action, dict) and action.get("status") in ("failed", "interrupted", "partial")
            for action in action_results.values()
        ):
            has_errors = True
            break
    final_status = "completed_with_errors" if has_errors else "completed"
    summary_parts = [f"{s['stage']}:{s['status']}" for s in stages]
    if log_id is not None:
        await _update_log(
            log_id,
            status=final_status,
            summary="; ".join(summary_parts),
            payload={"stages": stages, "target_date": local_today_str},
        )
    return True
