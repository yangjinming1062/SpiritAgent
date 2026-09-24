from datetime import datetime
from typing import Any

from components import DEFAULT_LANGUAGE, LLM_MAX_OUTPUT_TOKENS, get_logger, session_scope
from modules.conversation import Conversation, Message
from prompts.nightly import CHECKPOINT_SUMMARY_INSTRUCTIONS
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import run_prompt_json
from services.domains.conversation import (
    UI_ONLY_SUBTYPES,
    format_messages_compact,
    get_special_conversation,
    load_context_messages,
)
from services.domains.media import prune_videos_in_range
from services.infrastructure.llm import UserLlmConfig

logger = get_logger(__name__)

# 摘要通过 previous_summary 单独提供，不把多个检查点重复混入原始对话。
_NON_SUMMARISABLE_SUBTYPES = UI_ONLY_SUBTYPES | {"daily_summary", "compress_summary"}


def _summarisable_filter() -> Any:
    return Message.subtype.is_(None) | Message.subtype.notin_(tuple(_NON_SUMMARISABLE_SUBTYPES))


async def run_daily_checkpoint(
    llm_cfg: UserLlmConfig,
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
    local_date_str: str,
    language: str = DEFAULT_LANGUAGE,
) -> None:
    """合并最新摘要与截至目标本地日末的原文；模型等待期间的新消息仍保留在读路径。"""
    # 读、写两阶段各自持有短 session——中间 LLM 调用不能 pin 连接池（README §4 短事务规则）。
    async with session_scope() as db:
        inputs = await _collect_inputs(db, user_id, utc_start, utc_end)
    if inputs is None:
        return
    conv_id, chat_content, prev_summary_text, through_id, clear_watermark = inputs

    parsed, _ = await run_prompt_json(
        user_id,
        llm_cfg,
        CHECKPOINT_SUMMARY_INSTRUCTIONS,
        {
            "output_language": language,
            "summary_date": local_date_str,
            "previous_summary": prev_summary_text,
            "recent_conversation": chat_content,
        },
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        log_prefix="daily_checkpoint",
        temperature=0.0,
    )
    if not parsed:
        return
    raw_summary = parsed.get("summary")
    summary_text = raw_summary.strip() if isinstance(raw_summary, str) else ""
    if not summary_text:
        return

    async with session_scope() as wdb:
        conv = await wdb.get(Conversation, conv_id)
        if (
            conv is None
            or conv.context_after_message_id != clear_watermark
            or await wdb.get(Message, through_id) is None
        ):
            return
        checkpoint = Message(
            conversation_id=conv_id,
            role="system",
            content=f"[📝 截至 {local_date_str} 的对话摘要]\n{summary_text}",
            subtype="daily_summary",
            summary_date=local_date_str,
            summary_through_message_id=through_id,
        )
        wdb.add(checkpoint)
        await wdb.commit()
        await prune_videos_in_range(wdb, conv_id, hi=through_id + 1, preserve_queued=True)
        await wdb.commit()
    logger.info("daily_checkpoint: created summary", extra={"user_id": user_id, "date": local_date_str})


async def _collect_inputs(
    db: AsyncSession,
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
) -> tuple[int, str, str, int, int] | None:
    main_conv = await get_special_conversation(db, user_id, "companion")
    if main_conv is None:
        return

    # 当天至少要有一次真交互。
    real_turns = _summarisable_filter()
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
                real_turns,
            ),
        )
    ).scalar_one()
    if today_msg_count == 0:
        return

    history = await load_context_messages(db, main_conv)
    prev_checkpoint = next((m for m in history if m.subtype in ("daily_summary", "compress_summary")), None)
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
        format_messages_compact(rows),
        prev_summary_text,
        rows[-1].id,
        main_conv.context_after_message_id,
    )
