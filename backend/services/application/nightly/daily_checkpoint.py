from datetime import datetime
from zoneinfo import ZoneInfo

from components import LLM_MAX_OUTPUT_TOKENS, get_logger, parse_llm_json, resolve_prompt_text, session_scope, utc_now
from modules.conversation import Conversation, Message
from prompts.nightly import CHECKPOINT_SUMMARY_INSTRUCTIONS, CHECKPOINT_SUMMARY_TITLE_TEXTS
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.conversation import (
    CHECKPOINT_SUBTYPES,
    UI_ONLY_SUBTYPES,
    format_messages_compact,
    get_special_conversation,
    load_context_messages,
)
from services.domains.media import prune_videos_in_range
from services.infrastructure.llm import UserLlmConfig, call_llm_once

from .window import in_nightly_window

logger = get_logger(__name__)

# 摘要通过 previous_summary 单独提供，不把多个检查点重复混入原始对话。
_NON_SUMMARISABLE_SUBTYPES = UI_ONLY_SUBTYPES | frozenset(CHECKPOINT_SUBTYPES)


async def run_daily_checkpoint(
    llm_cfg: UserLlmConfig,
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
    local_date_str: str,
    language: str,
    *,
    user_timezone: str,
) -> bool:
    """合并最新摘要与截至目标本地日末的原文；模型等待期间的新消息仍保留在读路径。返回 ``True`` 已写入 / ``False`` 无可总结内容或历史已变化；模型调用失败或没有得到有效摘要时抛出。"""
    # 读、写两阶段各自持有短 session——中间 LLM 调用不能 pin 连接池（backend/README.md「数据与运行可靠性」）。
    async with session_scope() as db:
        inputs = await _collect_inputs(db, user_id, utc_start, utc_end, user_timezone=user_timezone)
    if inputs is None or not in_nightly_window(utc_now(), ZoneInfo(user_timezone)):
        return False
    conv_id, chat_content, prev_summary_text, through_id, clear_watermark = inputs

    raw = await call_llm_once(
        llm_cfg,
        CHECKPOINT_SUMMARY_INSTRUCTIONS,
        {
            "output_language": language,
            "summary_date": local_date_str,
            "user_timezone": user_timezone,
            "previous_summary": prev_summary_text,
            "recent_conversation": chat_content,
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        json_output=True,
        temperature=0.0,
    )
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("summary output is not an object")
    raw_summary = parsed.get("summary")
    summary_text = raw_summary.strip() if isinstance(raw_summary, str) else ""
    if not summary_text:
        raise ValueError("summary missing from model output")

    async with session_scope() as wdb:
        conv = await wdb.get(Conversation, conv_id)
        if (
            conv is None
            or conv.context_after_message_id != clear_watermark
            or await wdb.get(Message, through_id) is None
        ):
            logger.info("daily_checkpoint: history changed during summary", extra={"user_id": user_id})
            return False
        checkpoint = Message(
            conversation_id=conv_id,
            role="system",
            content=resolve_prompt_text(CHECKPOINT_SUMMARY_TITLE_TEXTS, language).format(date=local_date_str)
            + f"\n{summary_text}",
            subtype="daily_summary",
            summary_through_message_id=through_id,
        )
        wdb.add(checkpoint)
        await wdb.commit()
        await prune_videos_in_range(wdb, conv_id, hi=through_id + 1)
    logger.info("daily_checkpoint: created summary", extra={"user_id": user_id, "date": local_date_str})
    return True


async def _collect_inputs(
    db: AsyncSession,
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
    *,
    user_timezone: str,
) -> tuple[int, str, str, int, int] | None:
    main_conv = await get_special_conversation(db, user_id, "companion")
    if main_conv is None:
        return

    # 当天至少要有一次真交互。
    today_msg_count = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == main_conv.id,
                Message.id > main_conv.context_after_message_id,
                Message.created_at >= utc_start,
                Message.created_at < utc_end,
                Message.role.in_(("user", "assistant")),
                Message.subtype.is_(None) | Message.subtype.notin_(tuple(_NON_SUMMARISABLE_SUBTYPES)),
            ),
        )
    ).scalar_one()
    if today_msg_count == 0:
        return

    history = await load_context_messages(db, main_conv)
    prev_checkpoint = next((m for m in history if m.subtype in CHECKPOINT_SUBTYPES), None)
    rows = [m for m in history if m.subtype not in _NON_SUMMARISABLE_SUBTYPES and m.created_at < utc_end]
    # 未交付终端回复的回合继续保留原文，避免跨日工具结果与调用被摘要拆开。
    terminal_end = next(
        (i + 1 for i in range(len(rows) - 1, -1, -1) if rows[i].role == "assistant" and not rows[i].tool_calls),
        0,
    )
    rows = rows[:terminal_end]
    if not any(m.role in ("user", "assistant") for m in rows):
        return

    prev_summary_text = (prev_checkpoint.content or "") if prev_checkpoint else ""
    return (
        main_conv.id,
        format_messages_compact(rows, user_local_tz=user_timezone),
        prev_summary_text,
        rows[-1].id,
        main_conv.context_after_message_id,
    )
