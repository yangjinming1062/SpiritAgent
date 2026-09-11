import asyncio
import contextlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from components import (
    DEFAULT_LANGUAGE,
    MAX_AUTO_INJECT_CONTENT_CHARS,
    MAX_DIARY_CONTENT_CHARS,
    MAX_INFERRED_PROFILE_CONTENT_CHARS,
    MAX_RECALL_CONTENT_CHARS,
    NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
    NIGHTLY_CONSOLIDATION_MAX_TOKENS,
    NIGHTLY_DIARY_MAX_TOKENS,
    NIGHTLY_REFLECTION_MAX_TOKENS,
    get_logger,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    session_scope,
    utc_now,
)
from modules.auth import User
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import NightlyActivityLog
from modules.settings import UserSetting
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from services.companion import (
    AUTO_INJECT_SLOTS,
    INFERRED_PROFILE_SLOTS,
    KIND_TO_PREFIX,
    RECALL_TAGS,
    backfill_memory_embeddings,
    get_local_day_utc_bounds,
    is_injected_time_item,
    list_memories,
    prefilter_messages_for_nightly,
    project_today,
    read_today_summary,
    resolve_user_timezone,
    upsert_slotted_memory,
)
from services.conversation import SPECIAL_KIND, UI_ONLY_SUBTYPES
from services.llm import call_llm_once, resolve_user_llm_config

from .daily_checkpoint import run_daily_checkpoint
from .memory_consolidator import (
    RecallReplaceStatus,
    load_recall_snapshot,
    memory_consolidation_lock,
    replace_recall_pool,
)
from .nightly_planning import ActionExecutionResult, DateContext, PlanningResult, run_nightly_planning

logger = get_logger(__name__)

# 传给 planning 阶段的 recall 行数（用作高亮）。
_PLANNING_RECALL_HIGHLIGHTS: int = 10
# 转发到 reflection 阶段的工作会话轮次：陪伴会话是主信号，工作流量只用于挖掘兴趣。
_REFLECTION_MAX_WORK_MESSAGES: int = 50


_REFLECTION_SYSTEM_PROMPT = """You are SpiritAgent's nightly reflection engine. Analyze today's conversations between the user and their AI companion to extract durable user profile updates and assess relationship/emotional dynamics.

Today's conversations are split into two keys:
- "today_companion_conversations": Everyday companion conversation with the user — your main source for understanding user emotions, relationships, and preferences.
- "today_work_conversations": Work/task conversations — extract user technical interests, work habits, and schedule, but do NOT infer relationship/emotional state from work tasks.

Calendar date appears only in dividers before the first message of each local day (`--- Weekday, Month DD, YYYY ---`).
Each user message is followed by a separate clock/interval note, not the date. These are read-only metadata, not user speech.
Use dividers for calendar day and clock notes for time of day:
- Distinguish late-night vs daytime emotional context (e.g., user vents at 02:30 vs asks light questions at 14:00).
- Detect patterns like "user usually vents after midnight" or "user responds most actively in the evening".
- Correlate interaction intensity with time-of-day when updating `auto_inject:interaction_pattern` and `inferred_profile:work_schedule`.
Identify speakers by the `role` field; never treat time notes or date dividers as user utterances.

Instructions:
1. ONLY extract facts that are grounded in today's conversations or today's interaction statistics. Do NOT invent or assume facts.
2. Inferred Profile: Update the user's inferred profile in structured slots.
   Allowed inferred profile slots:
   - inferred_profile:basic_info (birthday, age group, location, occupation)
   - inferred_profile:work_schedule (working hours, routine, active times)
   - inferred_profile:interests (deeper interests, hobbies, technical topics)
   - inferred_profile:preferences (communication style, food, clothing, aesthetic preferences)
   - inferred_profile:important_dates (birthdays, anniversaries, exams, deadlines)
   - inferred_profile:relationships (important people, friends, family, colleagues)
   - inferred_profile:goals_stressors (current goals, aspirations, sources of stress)
   - inferred_profile:freeform (other rich profile facts that do not fit above)
3. Auto Inject: Update the companion's auto_inject slots based on today's rapport and emotional dynamics.
   Allowed auto_inject slots:
   - auto_inject:communication_style (how the user wants responses framed)
   - auto_inject:rapport_state (current relationship/familiarity stage)
   - auto_inject:interaction_pattern (user's typical use rhythm and habits)
   - auto_inject:mood_pattern (user's emotional tendency or state pattern)
   - auto_inject:relationship_signal (trust level, tease frequency, formality)
4. Only output slots where there is genuine new information or an update. Do not return empty updates.
5. Anti-Patterns:
   - Do NOT duplicate global directives (e.g. 'User speaks Chinese / prefers Chinese' — system handles default language).
   - Do NOT record companion's own persona (companion name, appearance, species, personality).
   - Do NOT duplicate static user profile facts already recorded in onboarding.
   - Do NOT record transient session data (PR numbers, commit hashes, temporary task states).
6. Interaction Statistics: The user message may include an "interaction_stats_today" field containing today's raw poke / chat counts and an hour_counts breakdown of when the user was active. This is grounded observational data (not conversation), so use it to inform:
   - auto_inject:interaction_pattern (e.g. heavy poking in a burst, late-night activity)
   - auto_inject:mood_pattern (e.g. restless poking may signal stress/boredom)
   - inferred_profile:work_schedule (active-hour distribution from hour_counts)
   Do NOT fabricate counts; only reflect what the field actually contains.

Output valid JSON only, in this exact schema:
{
  "inferred_profile_updates": [
    {"slot": "inferred_profile:basic_info", "content": "concise fact summary", "reason": "why updated"}
  ],
  "auto_inject_updates": [
    {"slot": "auto_inject:rapport_state", "content": "concise update under 500 chars"}
  ]
}
"""

_CONSOLIDATION_SYSTEM_PROMPT = """You are SpiritAgent's memory consolidation and decay engine. You are consolidating the user's recall-pool memories with the updated inferred profile as grounding context.

Instructions:
1. Merge duplicate or overlapping memory entries.
2. Remove outdated, contradicted, or decayed facts that are no longer relevant.
3. Remove anti-pattern entries if present (e.g. companion's own persona, default language rules like 'speaks Chinese', transient commit/PR progress, or duplicate onboarding profile fields).
4. Preserve durable, specific user facts and preferences.
5. When uncertain, KEEP the fact.
6. Each summary MUST use one closed-set tag from: {tags}.

Output valid JSON only, in this shape:
{{
  "summaries": [
    {{"content": "fact summary", "tags": ["one_allowed_tag"], "context": "short_topic_label"}}
  ]
}}
""".format(tags=", ".join(sorted(RECALL_TAGS)))

_STAGE4_DIARY_SYSTEM_TEXTS: dict[str, str] = {
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


async def _stage_1_daily_reflection(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    user_profile: dict[str, str],
    local_date_str: str,
    clean_work_messages: list[dict[str, str]] | None = None,
    interaction_stats_today: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Stage 1：每日反思——更新 inferred_profile 和 auto_inject。"""
    payload = {
        "today_companion_conversations": clean_messages,
        # 列表是升序，最新的工作轮比早上更重要。
        "today_work_conversations": (clean_work_messages or [])[-_REFLECTION_MAX_WORK_MESSAGES:],
        "current_inferred_profile": inferred_profile,
        "current_auto_inject": auto_inject,
        "user_profile": user_profile,
        "local_date": local_date_str,
    }
    if interaction_stats_today is not None:
        payload["interaction_stats_today"] = interaction_stats_today
    raw = await call_llm_once(
        llm_cfg,
        _REFLECTION_SYSTEM_PROMPT,
        payload,
        max_output_tokens=NIGHTLY_REFLECTION_MAX_TOKENS,
    )
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning(
            "nightly_activity: stage 1 failed to parse JSON",
            extra={"user_id": user_id},
        )
        return inferred_profile, auto_inject

    inferred_updates = parsed.get("inferred_profile_updates") or []
    auto_inject_updates = parsed.get("auto_inject_updates") or []

    updated_inferred = dict(inferred_profile)
    updated_auto_inject = dict(auto_inject)
    async with session_scope() as db:
        if isinstance(inferred_updates, list):
            for item in inferred_updates:
                if not isinstance(item, dict):
                    continue
                slot = item.get("slot")
                content = (item.get("content") or "").strip()
                if slot not in INFERRED_PROFILE_SLOTS or not content:
                    continue
                content_truncated = content[:MAX_INFERRED_PROFILE_CONTENT_CHARS]
                await upsert_slotted_memory(
                    db,
                    user_id,
                    slot,
                    content_truncated,
                    json.dumps(["inferred_profile"]),
                )
                updated_inferred[slot] = content_truncated

        if isinstance(auto_inject_updates, list):
            for item in auto_inject_updates:
                if not isinstance(item, dict):
                    continue
                slot = item.get("slot")
                content = (item.get("content") or "").strip()
                if slot not in AUTO_INJECT_SLOTS or not content:
                    continue
                content_truncated = content[:MAX_AUTO_INJECT_CONTENT_CHARS]
                await upsert_slotted_memory(
                    db,
                    user_id,
                    slot,
                    content_truncated,
                    json.dumps(["auto_inject"]),
                )
                updated_auto_inject[slot] = content_truncated
        await db.commit()

    logger.info("nightly_activity: stage 1 completed", extra={"user_id": user_id})
    return updated_inferred, updated_auto_inject


async def _stage_2_memory_consolidation(
    llm_cfg: dict[str, Any],
    user_id: int,
    inferred_profile: dict[str, str],
    local_date_str: str,
) -> bool:
    """Stage 2：记忆合并与衰减。"""
    # Stage 1 也会等待 LLM，不能复用流水线起点读取的 recall_rows；取得与白天整理共用的
    # 进程内锁后重新取快照，且在 LLM await 前关闭数据库会话。
    async with memory_consolidation_lock(user_id):
        async with session_scope() as db:
            source_rows = await load_recall_snapshot(
                db,
                user_id,
                limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
            )
        if not source_rows:
            logger.info(
                "nightly_activity: stage 2 skipped, recall pool empty",
                extra={"user_id": user_id},
            )
            return True

        payload = {
            "recall_pool": [row.prompt_row() for row in source_rows],
            "inferred_profile": inferred_profile,
            "local_date": local_date_str,
        }
        raw = await call_llm_once(
            llm_cfg,
            _CONSOLIDATION_SYSTEM_PROMPT,
            payload,
            max_output_tokens=NIGHTLY_CONSOLIDATION_MAX_TOKENS,
        )
        parsed = parse_llm_json(raw)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
            logger.warning(
                "nightly_activity: stage 2 failed to parse summaries",
                extra={"user_id": user_id},
            )
            return False

        result = await replace_recall_pool(user_id, source_rows, parsed["summaries"])
        if result.status == RecallReplaceStatus.EMPTY_SUMMARIES:
            logger.warning(
                "nightly_activity: stage 2 all summaries empty, source rows preserved",
                extra={"user_id": user_id},
            )
            return False
        if result.status == RecallReplaceStatus.STALE_SNAPSHOT:
            logger.info(
                "nightly_activity: stage 2 source snapshot changed, old summaries discarded",
                extra={"user_id": user_id},
            )
            return False

        logger.info(
            "nightly_activity: stage 2 completed",
            extra={
                "user_id": user_id,
                "replaced": len(source_rows),
                "written": result.written,
            },
        )
        return True


async def _stage_4_self_diary(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    local_date_str: str,
    autonomous_actions: list[dict[str, Any]],
    language: str = DEFAULT_LANGUAGE,
) -> bool:
    """Stage 4：自我日记——伙伴写下当天的个人反思。"""
    payload = {
        "today_conversations": clean_messages,
        "inferred_profile": inferred_profile,
        "auto_inject": auto_inject,
        "nightly_autonomous_actions": autonomous_actions,
        "local_date": local_date_str,
        "language": language,
    }
    raw = await call_llm_once(
        llm_cfg,
        resolve_prompt_text(_STAGE4_DIARY_SYSTEM_TEXTS, language),
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
            user_id,
            diary_context,
            content,
            json.dumps(["diary", "self_reflection"]),
        )
        await db.commit()
    # diary 命名空间参与 recall 检索，落库后补向量（best-effort，不阻塞夜间流水线）。
    await backfill_memory_embeddings(user_id, [(row.id, row.content)])

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
    user_id: int,
    local_date_str: str,
    actions: list[dict[str, Any]],
    language: str = DEFAULT_LANGUAGE,
) -> None:
    """把精灵真正做过的事写入可检索自传记忆，供次日对话自然延续。"""
    if not actions:
        return
    prefix = "Nightly autonomous activities: " if language == "en" else "夜间自主活动："
    content = prefix + "；".join(str(item["fact"]) for item in actions)
    async with session_scope() as db:
        row = await upsert_slotted_memory(
            db,
            user_id,
            f"recall:nightly_actions:{local_date_str}",
            content[:MAX_RECALL_CONTENT_CHARS],
            json.dumps(["other"]),
        )
        await db.flush()
        embedding_item = (row.id, row.content)
        await db.commit()
    await backfill_memory_embeddings(user_id, [embedding_item])


async def run_nightly_pipeline(
    user_id: int,
    reference_utc: datetime | None = None,
    *,
    target_date: date | None = None,
) -> bool:
    """为单用户执行四阶段夜间自主活动流水线；reference_utc 决定处理哪个本地日——cron 门控传刚结束的当天，边界与日期标签由同一 instant 派生，避免算两次发生漂移。每次执行（含跳过）写入一条 nightly_activity_logs 行供管理员查看。"""
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
        return await _run_nightly_pipeline_inner(user_id, now_utc, log_id)
    except Exception as exc:
        # DB 写失败不应掩盖原始异常——cron 的 logger 已有 exc_info，这里只尽力留个 failed 行供 admin 看。
        if log_id is not None:
            with contextlib.suppress(Exception):
                await _update_log(log_id, status="failed", summary=f"未捕获异常: {exc}")
        raise


async def _run_nightly_pipeline_inner(
    user_id: int,
    now_utc: datetime,
    log_id: int | None,
) -> bool:
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
                    Conversation.is_automation.is_(False),
                    Message.created_at >= utc_start,
                    Message.created_at < utc_end,
                    Message.role.in_(("user", "assistant")),
                    Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                )
                .order_by(Message.id.asc()),
            )
        ).all()
        main_msgs = [m for m, p in all_today_tuples if p == "companion"]
        work_msgs = [m for m, p in all_today_tuples if p != "companion"]

        clean_main_messages = prefilter_messages_for_nightly(main_msgs, user_tz=tz_str)
        clean_work_messages = prefilter_messages_for_nightly(work_msgs, user_tz=tz_str)
        # 跨两类按时间顺序——日记 prompt 把这一天的对话视为整体，简单拼接会凭空造出从未发生的顺序。
        clean_messages = prefilter_messages_for_nightly(
            [m for m, _ in all_today_tuples],
            user_tz=tz_str,
        )
        has_user_messages = any(m["role"] == "user" and not is_injected_time_item(m) for m in clean_messages)

        # 加载已有 memory 命名空间——一个 query 取三种前缀。
        ns_rows = (
            (
                await db.execute(
                    select(Memory).where(
                        Memory.user_id == user_id,
                        Memory.context.like(KIND_TO_PREFIX["inferred_profile"] + "%")
                        | Memory.context.like(KIND_TO_PREFIX["auto_inject"] + "%")
                        | Memory.context.like(KIND_TO_PREFIX["user_profile"] + "%"),
                    ),
                )
            )
            .scalars()
            .all()
        )
        inferred_profile: dict[str, str] = {}
        auto_inject: dict[str, str] = {}
        user_profile: dict[str, str] = {}
        for r in ns_rows:
            if r.context.startswith(KIND_TO_PREFIX["inferred_profile"]):
                inferred_profile[r.context] = r.content
            elif r.context.startswith(KIND_TO_PREFIX["auto_inject"]):
                auto_inject[r.context] = r.content
            elif r.context.startswith(KIND_TO_PREFIX["user_profile"]):
                user_profile[r.context] = r.content

        recall_rows = await list_memories(
            db,
            user_id,
            kind="recall",
            limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
        )

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
    updated_inferred = inferred_profile
    updated_auto_inject = auto_inject
    today_stats = await read_today_summary(user_id, local_today_str)
    if has_user_messages or today_stats:
        try:
            updated_inferred, updated_auto_inject = await _stage_1_daily_reflection(
                llm_cfg,
                user_id,
                clean_main_messages,
                inferred_profile,
                auto_inject,
                user_profile,
                local_today_str,
                clean_work_messages=clean_work_messages,
                interaction_stats_today=today_stats,
            )
            stages.append({"stage": "reflection", "status": "ok"})
        except Exception as exc:
            logger.exception(
                "nightly_activity: stage 1 reflection failed",
                extra={"user_id": user_id, "error": str(exc)},
            )
            stages.append({"stage": "reflection", "status": "error", "error": str(exc)})
    else:
        stages.append(
            {"stage": "reflection", "status": "skipped", "reason": "当日无新互动"},
        )

    try:
        consolidation_ok = await _stage_2_memory_consolidation(
            llm_cfg,
            user_id,
            updated_inferred,
            local_today_str,
        )
        stages.append(
            {
                "stage": "consolidation",
                "status": "ok" if consolidation_ok else "error",
            },
        )
    except Exception as exc:
        logger.exception(
            "nightly_activity: stage 2 consolidation failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        stages.append({"stage": "consolidation", "status": "error", "error": str(exc)})

    planning_result = PlanningResult()
    action_facts: list[dict[str, Any]] = []
    try:
        recall_highlights = recall_rows[:_PLANNING_RECALL_HIGHLIGHTS] if recall_rows else []
        planning_result = await run_nightly_planning(
            llm_cfg,
            user_id,
            updated_inferred,
            updated_auto_inject,
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
            await _write_action_memory(user_id, local_today_str, action_facts, language=user_language)
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
                user_id,
                clean_messages,
                updated_inferred,
                updated_auto_inject,
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
