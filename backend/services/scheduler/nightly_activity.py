import asyncio
import contextlib
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from components import (
    MAX_AUTO_INJECT_CONTENT_CHARS,
    MAX_DIARY_CONTENT_CHARS,
    MAX_INFERRED_PROFILE_CONTENT_CHARS,
    NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS,
    NIGHTLY_CONSOLIDATION_MAX_TOKENS,
    NIGHTLY_CREATION_MAX_EXPRESSIONS_PER_NIGHT,
    NIGHTLY_CREATION_MAX_TOKENS,
    NIGHTLY_DIARY_MAX_TOKENS,
    NIGHTLY_PLANNING_MAX_TOKENS,
    NIGHTLY_REFLECTION_MAX_TOKENS,
    get_logger,
    parse_llm_json,
    safe_json_loads,
    session_scope,
    utc_now,
)
from modules.companion import CompanionExpression, Persona
from modules.conversation import Conversation, Message
from modules.memory import Memory
from modules.scheduler import NightlyActivityLog
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
    validate_and_sanitize_expression,
)
from services.conversation import CRON_KIND, SPECIAL_KIND, UI_ONLY_SUBTYPES
from services.llm import call_llm_once, resolve_user_llm_config

from .cron_jobs import create_job
from .daily_checkpoint import run_daily_checkpoint
from .memory_consolidator import replace_recall_pool

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

_PLANNING_SYSTEM_PROMPT = """You are SpiritAgent's proactive planning engine — the companion's "inner voice" deciding what autonomous actions to take tomorrow. You have genuine subjective initiative: the companion is a living being with moods, curiosity, and care for the user, not a scripted bot.

Your task:
Review the user's profile, emotional/rapport state, recent conversations, and upcoming dates to decide whether to schedule a proactive conversational outreach for tomorrow.

Context you receive:
- "today_conversations": The last messages between user and companion — pay special attention to how the conversation ended (goodnight exchange, emotional moment, unresolved topic, casual drift, etc.)
- "inferred_profile": What the companion knows about the user
- "auto_inject_state": Current rapport, mood patterns, interaction style
- "recall_highlights": Recent memory fragments
- Date context: tomorrow's date, weekday, next 7 days

Decision framework:
- If the last exchange was a warm goodnight, a heartfelt moment, or left something open → a natural morning touchpoint is appropriate
- If the user was stressed or upset late at night → a gentle check-in shows care
- If the conversation was routine/casual → a greeting may or may not be needed; trust your instinct
- If the user was silent or distant → don't force interaction; sometimes space is the caring choice
- NEVER use keyword matching (like looking for "晚安" or "good night"); judge by the emotional arc and conversational flow
- You decide WHEN to reach out: it could be 8am, 10am, noon, or not at all — pick a time that feels natural given the context
- You decide WHAT to say: it can be as simple as "早安" or as personal as referencing last night's topic — match the emotional tone
- You can also schedule a proactive check-in at any time of day if the context calls for it (e.g. reminding the user about something they mentioned, following up on an emotional moment, sharing a thought)
- The outreach must feel like a real person deciding to reach out, not a scheduled notification

Conservative principle:
- Only schedule actions with genuine grounding in the relationship context
- An empty actions list {"actions": []} is perfectly valid when nothing feels right
- Quality over quantity: one heartfelt action beats three mechanical ones

When scheduling an action:
- "name": Short label (e.g. "早安问好", "晚安跟进", "关心考试", "延续昨晚话题")
- "schedule": A standard 5-field cron expression in UTC time. Convert from user's local time — you know their timezone.
- "prompt": An actionable instruction for the companion's autonomous turn. Write it as if speaking to the companion: what tone to take, what context to reference, what emotional register to use. The companion will deliver this as a natural chat message.

Output valid JSON only:
{
  "actions": [
    {"name": "...", "schedule": "cron_expression", "prompt": "..."}
  ]
}
"""

_DIARY_SYSTEM_PROMPT = """You are the AI companion reflecting privately at the end of the day. Write a personal diary entry (in the companion's first person '我') reflecting on today's interactions with the user.

Guidelines:
- Tone: Natural, reflective, caring, with emotional continuity.
- Content: What you learned about the user today, moments shared, thoughts on your relationship, or what you look forward to.
- Length: Keep it under 1000 characters.
- Date dividers and system time notes in the conversation log are metadata, not user speech.

Output valid JSON only:
{
  "content": "日记正文..."
}
"""

_CREATION_SYSTEM_PROMPT = """You are SpiritAgent's autonomous asset creation engine. Analyze today's interactions, the companion's private diary, current user profile, personality tags, and existing expressions to identify specific moments where the companion wanted to express something but lacked a matching expression.

Conservative Principle:
- ONLY create assets if there is a concrete, grounded moment from today's conversation where the companion lacked an effective emotional expression.
- If existing expressions are already sufficient or today was routine, return empty lists: {{"gaps": []}}.
- Do NOT generate generic or repetitive assets.

Asset specifications:
1. "gaps": List of identified expression gaps (max 3). Each item produces:
   - "moment": Brief explanation of the specific moment today.
   - "want_to_express": Emotional intent.
   - "expression": Custom emotion object:
     {{
       "name": "snake_case_name", // e.g. "tender_worry"
       "label": "心疼",
       "valence": "positive" | "negative" | "neutral",
       "description": "What the companion's face looks like in this emotion — drives the generated expression avatar image",
       "icon": "🥺", // optional single emoji
       "tags": ["温柔", "心疼"]
     }}
   - "tags": ["温柔", "心疼"]

Output valid JSON only:
{{
  "gaps": [...]
}}
"""


def _local_9am_cron(tz_str: str | None) -> str:
    """构造"明天 09:00 本地"换算成 UTC 后的 5 字段 cron；时区无法解析时回落到 0 1 * * *（接近 09:00 UTC），让行为受控而非默认错。"""
    if not tz_str:
        return "0 1 * * *"
    try:
        zone = ZoneInfo(tz_str)
        now_local = utc_now().astimezone(zone)
        tomorrow_9am = (now_local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        tomorrow_utc = tomorrow_9am.astimezone(ZoneInfo("UTC"))
        return f"{tomorrow_utc.minute} {tomorrow_utc.hour} * * *"
    except (ZoneInfoNotFoundError, ValueError):
        return "0 1 * * *"


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
    raw = await call_llm_once(llm_cfg, _REFLECTION_SYSTEM_PROMPT, payload, max_output_tokens=NIGHTLY_REFLECTION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 1 failed to parse JSON", extra={"user_id": user_id})
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
                await upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["inferred_profile"]))
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
                await upsert_slotted_memory(db, user_id, slot, content_truncated, json.dumps(["auto_inject"]))
                updated_auto_inject[slot] = content_truncated
        await db.commit()

    logger.info("nightly_activity: stage 1 completed", extra={"user_id": user_id})
    return updated_inferred, updated_auto_inject


async def _stage_2_memory_consolidation(llm_cfg: dict[str, Any], user_id: int, recall_rows: list[dict[str, Any]], inferred_profile: dict[str, str], local_date_str: str) -> bool:
    """Stage 2：记忆合并与衰减。"""
    if not recall_rows:
        logger.info("nightly_activity: stage 2 skipped, recall pool empty", extra={"user_id": user_id})
        return True

    payload = {"recall_pool": recall_rows, "inferred_profile": inferred_profile, "local_date": local_date_str}
    raw = await call_llm_once(llm_cfg, _CONSOLIDATION_SYSTEM_PROMPT, payload, max_output_tokens=NIGHTLY_CONSOLIDATION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("summaries"), list):
        logger.warning("nightly_activity: stage 2 failed to parse summaries", extra={"user_id": user_id})
        return False

    summaries = parsed["summaries"]
    written = await replace_recall_pool(user_id, recall_rows, summaries)
    if written <= 0:
        logger.warning("nightly_activity: stage 2 all summaries empty, source rows preserved", extra={"user_id": user_id})
        return False

    logger.info("nightly_activity: stage 2 completed", extra={"user_id": user_id, "replaced": len(recall_rows), "written": written})
    return True


async def _stage_3_planning(
    llm_cfg: dict[str, Any],
    user_id: int,
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    recall_highlights: list[dict[str, Any]],
    date_context: dict[str, Any],
    anomaly_stats: dict[str, Any],
    today_conversations: list[dict[str, str]] | None = None,
) -> int:
    """Stage 3：计划——在合适时创建主动触达的 CronJob。"""
    payload = {
        "inferred_profile": inferred_profile,
        "auto_inject_state": auto_inject,
        "recall_highlights": recall_highlights,
        "today_conversations": today_conversations or [],
        **date_context,
        **anomaly_stats,
    }
    raw = await call_llm_once(llm_cfg, _PLANNING_SYSTEM_PROMPT, payload, max_output_tokens=NIGHTLY_PLANNING_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("actions"), list):
        logger.info("nightly_activity: stage 3 no actions parsed", extra={"user_id": user_id})
        return 0

    actions = parsed["actions"]
    created_count = 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        prompt = (action.get("prompt") or "").strip()
        schedule = (action.get("schedule") or "").strip()
        name = (action.get("name") or "proactive outreach").strip()
        if not prompt or not schedule:
            continue
        try:
            await create_job(user_id=user_id, prompt=prompt, schedule=schedule, name=name, deliver="local")
            created_count += 1
        except (ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: stage 3 create_job skipped", extra={"user_id": user_id, "error": str(exc)})

    logger.info("nightly_activity: stage 3 completed", extra={"user_id": user_id, "created_jobs": created_count})
    return created_count


async def _stage_4_self_diary(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    local_date_str: str,
) -> bool:
    """Stage 4：自我日记——伙伴写下当天的个人反思。"""
    payload = {"today_conversations": clean_messages, "inferred_profile": inferred_profile, "auto_inject": auto_inject, "local_date": local_date_str}
    raw = await call_llm_once(llm_cfg, _DIARY_SYSTEM_PROMPT, payload, max_output_tokens=NIGHTLY_DIARY_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 4 failed to parse diary JSON", extra={"user_id": user_id})
        return False

    content = (parsed.get("content") or "").strip()[:MAX_DIARY_CONTENT_CHARS]
    if not content:
        return False

    diary_context = f"diary:{local_date_str}"
    async with session_scope() as db:
        row = await upsert_slotted_memory(db, user_id, diary_context, content, json.dumps(["diary", "self_reflection"]))
        await db.commit()
    # diary 命名空间参与 recall 检索，落库后补向量（best-effort，不阻塞夜间流水线）。
    await backfill_memory_embeddings(user_id, [(row.id, row.content)])

    logger.info("nightly_activity: stage 4 completed", extra={"user_id": user_id, "diary": diary_context})
    return True


async def _stage_5_creation(
    llm_cfg: dict[str, Any],
    user_id: int,
    clean_messages: list[dict[str, str]],
    inferred_profile: dict[str, str],
    auto_inject: dict[str, str],
    local_date_str: str,
    tz_str: str | None = None,
) -> bool:
    """Stage 5：自主创作——伙伴生成新表情。"""

    async with session_scope() as db:
        # 取 Stage 4 的日记
        diary_context = f"diary:{local_date_str}"
        diary_row = (await db.execute(select(Memory).where(Memory.user_id == user_id, Memory.context == diary_context))).scalar_one_or_none()
        diary_text = diary_row.content if diary_row else ""

        # 取 persona 人格标签
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        personality_tags = safe_json_loads(persona.personality_tags_json or "[]", default=[]) if persona else []

        # 检查已有表情
        existing_expr_rows = (await db.execute(select(CompanionExpression).where(CompanionExpression.user_id == user_id))).scalars().all()
        existing_expr_names = [e.name for e in existing_expr_rows]

    # 构造创作 prompt
    system_prompt = _CREATION_SYSTEM_PROMPT
    payload = {
        "today_conversations": clean_messages,
        "companion_diary": diary_text,
        "inferred_profile": inferred_profile,
        "personality_tags": personality_tags,
        "existing_expressions": existing_expr_names,
    }

    raw = await call_llm_once(llm_cfg, system_prompt, payload, max_output_tokens=NIGHTLY_CREATION_MAX_TOKENS)
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("nightly_activity: stage 5 failed to parse JSON", extra={"user_id": user_id})
        return False

    gaps = parsed.get("gaps") or []

    new_expr_count = 0

    if isinstance(gaps, list):
        pending_expressions: list[dict[str, Any]] = []
        for gap in gaps:
            if not isinstance(gap, dict) or len(pending_expressions) >= NIGHTLY_CREATION_MAX_EXPRESSIONS_PER_NIGHT:
                continue
            expr_spec = gap.get("expression")
            if isinstance(expr_spec, dict):
                sanitized_expr = validate_and_sanitize_expression(expr_spec)
                if sanitized_expr and sanitized_expr["name"] not in existing_expr_names:
                    pending_expressions.append(sanitized_expr)
                    existing_expr_names.append(sanitized_expr["name"])

        async with session_scope() as db:
            for expr in pending_expressions:
                db.add(
                    CompanionExpression(
                        user_id=user_id,
                        name=expr["name"],
                        label=expr["label"],
                        valence=expr["valence"],
                        description=expr["description"],
                        icon=expr.get("icon"),
                        tags_json=json.dumps(expr["tags"], ensure_ascii=False),
                    ),
                )
                new_expr_count += 1
            await db.commit()

    # 2. 若生成了资产，安排早晨通知的 cron job
    if new_expr_count > 0:
        cron_prompt = f"昨晚你默默为用户完成了一轮创作（创造了 {new_expr_count} 个新表情）。在今天的聊天中，请自然地展示你的新表情。"

        # 安排到下次本地 09:00——cron 用 UTC，从用户时区换算；one_shot=True 让 job 触发后删除，一次性"展示新创作"消息不会每天重复。
        schedule = _local_9am_cron(tz_str)
        try:
            await create_job(user_id=user_id, prompt=cron_prompt, schedule=schedule, name="Creation gift follow-up", deliver="local", one_shot=True)
        except Exception as exc:
            logger.warning("nightly_activity: stage 5 cron creation failed", extra={"user_id": user_id, "error": str(exc)})

    logger.info("nightly_activity: stage 5 completed", extra={"user_id": user_id, "expressions": new_expr_count})
    return True


async def _update_log(log_id: int, **kwargs: Any) -> None:
    """在独立事务中更新夜间活动日志行——主流水线的 session 在 Stage 0 之后已关闭，且各阶段在独立事务里跑，复用同一 session 会与 lifecycle 错位。"""
    async with session_scope() as db:
        log = await db.get(NightlyActivityLog, log_id)
        if log:
            for k, v in kwargs.items():
                setattr(log, k, v)
            await db.commit()


async def run_nightly_pipeline(user_id: int, reference_utc: datetime | None = None) -> bool:
    """为单用户执行 5 阶段夜间自主活动流水线；reference_utc 决定处理哪个本地日——cron 门控传刚结束的当天，边界与日期标签由同一 instant 派生，避免算两次发生漂移。每次执行（含跳过）写入一条 nightly_activity_logs 行供管理员查看。"""
    now_utc = reference_utc or utc_now()

    log_id: int | None = None
    try:
        async with session_scope() as db:
            log = NightlyActivityLog(user_id=user_id, target_date=now_utc.date(), status="running")
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = log.id
    except Exception:
        logger.warning("nightly_activity: failed to create log row", extra={"user_id": user_id})

    try:
        return await _run_nightly_pipeline_inner(user_id, now_utc, log_id)
    except Exception as exc:
        # DB 写失败不应掩盖原始异常——cron 的 logger 已有 exc_info，这里只尽力留个 failed 行供 admin 看。
        if log_id is not None:
            with contextlib.suppress(Exception):
                await _update_log(log_id, status="failed", summary=f"未捕获异常: {exc}")
        raise


async def _run_nightly_pipeline_inner(user_id: int, now_utc: datetime, log_id: int | None) -> bool:
    async with session_scope() as db:
        tz_str = await resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info("nightly_activity: skipped, missing timezone", extra={"user_id": user_id})
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary="缺少时区设置")
            return False
        try:
            utc_start, utc_end, user_local_dt, local_today_str = get_local_day_utc_bounds(now_utc, tz_str)
        except (ZoneInfoNotFoundError, ValueError, SQLAlchemyError) as exc:
            logger.warning("nightly_activity: timezone resolution error", extra={"user_id": user_id, "error": str(exc)})
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary=f"时区解析错误: {exc}")
            return False

        # Stage 0：收集上下文
        all_today_tuples = (
            await db.execute(
                select(Message, Conversation.system_preset_id)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Conversation.kind != CRON_KIND,
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
        # 跨两类按时间顺序——日记和创作 prompt 把这一天的对话视为整体，简单拼接会凭空造出从未发生的顺序。
        clean_messages = prefilter_messages_for_nightly([m for m, _ in all_today_tuples], user_tz=tz_str)
        if not any(m["role"] == "user" and not is_injected_time_item(m) for m in clean_messages):
            logger.info("nightly_activity: no clean user messages today", extra={"user_id": user_id})
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary="当日无用户消息", target_date=user_local_dt.date())
            return False

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

        recall_rows = await list_memories(db, user_id, kind="recall", limit=NIGHTLY_CONSOLIDATE_MAX_RECALL_ROWS)

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
        date_context = {
            "tomorrow_date": tomorrow_dt.strftime("%Y-%m-%d"),
            "tomorrow_weekday": tomorrow_dt.strftime("%A"),
            "next_7_days": [(user_local_dt + timedelta(days=i)).strftime("%Y-%m-%d (%A)") for i in range(1, 8)],
            "user_timezone": tz_str,
        }
        anomaly_stats = {"today_msg_count": today_msg_count, "seven_day_avg": seven_day_avg}

        llm_cfg = await resolve_user_llm_config(db, user_id)
        if not (llm_cfg.get("api_key") and llm_cfg.get("base_url") and llm_cfg.get("model_name")):
            logger.info("nightly_activity: skipped, missing llm config", extra={"user_id": user_id})
            if log_id is not None:
                await _update_log(log_id, status="skipped", summary="缺少 LLM 配置")
            return False

    # 修正 target_date 为用户本地日——日志创建时仅拿到 UTC 日期，时区解析后才知用户本地日。
    if log_id is not None:
        await _update_log(log_id, target_date=user_local_dt.date())

    # 各阶段顺序执行，失败域相互隔离；每个阶段的结果汇入 stages 给末尾日志。
    stages: list[dict[str, Any]] = []
    updated_inferred = inferred_profile
    updated_auto_inject = auto_inject
    today_stats = await read_today_summary(user_id, local_today_str)
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
        logger.exception("nightly_activity: stage 1 reflection failed", extra={"user_id": user_id, "error": str(exc)})
        stages.append({"stage": "reflection", "status": "error", "error": str(exc)})

    try:
        await _stage_2_memory_consolidation(llm_cfg, user_id, recall_rows, updated_inferred, local_today_str)
        stages.append({"stage": "consolidation", "status": "ok"})
    except Exception as exc:
        logger.exception("nightly_activity: stage 2 consolidation failed", extra={"user_id": user_id, "error": str(exc)})
        stages.append({"stage": "consolidation", "status": "error", "error": str(exc)})

    planning_count = 0
    try:
        recall_highlights = recall_rows[:_PLANNING_RECALL_HIGHLIGHTS] if recall_rows else []
        planning_count = await _stage_3_planning(
            llm_cfg,
            user_id,
            updated_inferred,
            updated_auto_inject,
            recall_highlights,
            date_context,
            anomaly_stats,
            today_conversations=clean_main_messages,
        )
        stages.append({"stage": "planning", "status": "ok", "created_jobs": planning_count})
    except Exception as exc:
        logger.exception("nightly_activity: stage 3 planning failed", extra={"user_id": user_id, "error": str(exc)})
        stages.append({"stage": "planning", "status": "error", "error": str(exc)})

    try:
        await _stage_4_self_diary(llm_cfg, user_id, clean_messages, updated_inferred, updated_auto_inject, local_today_str)
        stages.append({"stage": "diary", "status": "ok"})
    except Exception as exc:
        logger.exception("nightly_activity: stage 4 diary failed", extra={"user_id": user_id, "error": str(exc)})
        stages.append({"stage": "diary", "status": "error", "error": str(exc)})

    # Daily checkpoint 和 stage-5 creation 相互独立（独立 session_scope、独立 LLM 调用、无共享状态）——并发跑，每用户每晚省一次 LLM 往返墙钟时间。
    async def _checkpoint() -> None:
        await run_daily_checkpoint(llm_cfg, user_id, utc_start, utc_end, local_today_str)

    async def _journal_project() -> bool | None:
        return await project_today(user_id, reference_utc=now_utc, pre_messages=clean_main_messages, llm_cfg=llm_cfg)

    results = await asyncio.gather(
        _checkpoint(),
        _stage_5_creation(llm_cfg, user_id, clean_messages, updated_inferred, updated_auto_inject, local_today_str, tz_str=tz_str),
        _journal_project(),
        return_exceptions=True,
    )
    for label, result in zip(("daily checkpoint", "stage 5 creation", "journal nightly"), results, strict=True):
        if isinstance(result, Exception):
            # 不在 except 块里——必须显式传异常，否则 exc_info 为空，traceback 丢失。
            logger.error(f"nightly_activity: {label} failed", exc_info=result, extra={"user_id": user_id})
            stages.append({"stage": label, "status": "error", "error": str(result)})
        elif label == "journal nightly" and result is None:
            logger.error(
                "nightly_activity: journal nightly failed",
                extra={"user_id": user_id, "error": "empty compose result"},
            )
            stages.append({"stage": label, "status": "error", "error": "empty compose result"})
        else:
            stages.append({"stage": label, "status": "ok"})

    has_errors = any(s.get("status") == "error" for s in stages)
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
