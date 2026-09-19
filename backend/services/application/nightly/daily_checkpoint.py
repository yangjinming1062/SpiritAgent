from datetime import datetime
from typing import Any

from components import DEFAULT_LANGUAGE, get_logger, session_scope
from modules.conversation import Message
from prompts.nightly import CHECKPOINT_SUMMARY_INSTRUCTIONS
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.domains.companion import run_prompt_json
from services.domains.conversation import UI_ONLY_SUBTYPES, format_messages_compact, get_special_conversation
from services.domains.media import prune_videos_in_range
from services.infrastructure.llm import UserLlmConfig

logger = get_logger(__name__)

_SUMMARY_MAX_TOKENS = 800

# Checkpoint 子类型——daily_summary（nightly）和 compress_summary（运行时 token 阈值）。两者在 LLM 读路径都充当"我之前的内容已被摘要"。
_CHECKPOINT_SUBTYPES: frozenset[str] = frozenset({"daily_summary", "compress_summary"})

# 排除在 daily-summary 输入之外的 subtypes：UI-only 占位、之前的 daily_summary checkpoint（通过 prev_summary_block 单独喂入）。compress_summary 要保留——其内容必须前滚到新的 daily_summary，新行取代它作为读起点 checkpoint 后不能丢。
_NON_SUMMARISABLE_SUBTYPES: frozenset[str] = frozenset(UI_ONLY_SUBTYPES | {"daily_summary"})


def _summarisable_filter() -> Any:
    return Message.subtype.is_(None) | Message.subtype.notin_(tuple(_NON_SUMMARISABLE_SUBTYPES))


def _gap_days(prev_date_str: str | None, today_str: str) -> int | None:
    if not prev_date_str:
        return None
    try:
        return (datetime.strptime(today_str, "%Y-%m-%d") - datetime.strptime(prev_date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


async def run_daily_checkpoint(
    llm_cfg: UserLlmConfig | dict[str, Any],
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
    local_date_str: str,
    language: str = DEFAULT_LANGUAGE,
) -> None:
    """生成每日上下文检查点：从最近 checkpoint（daily_summary 或 compress_summary，inclusive）到现在，调一次 LLM 压成新 daily_summary；compress_summary 行被 summarisable filter 包含进 chat_content，由 LLM 融入新 daily_summary——内容不丢；新行 id 更大自动取代旧 compress_summary 成后续读起点；触发=当天≥1 真交互，跳过=无真交互或最近 checkpoint 后无新行；只追加不删除。"""
    # 读、写两阶段各自持有短 session——中间 LLM 调用不能 pin 连接池（README §4 短事务规则）。
    async with session_scope() as db:
        inputs = await _collect_inputs(db, user_id, utc_start, utc_end, local_date_str)
    if inputs is None:
        return
    conv_id, chat_content, prev_summary_text, conversation_gap = inputs

    parsed, _ = await run_prompt_json(
        user_id,
        llm_cfg,
        CHECKPOINT_SUMMARY_INSTRUCTIONS,
        {
            "output_language": language,
            "previous_summary": prev_summary_text,
            "recent_conversation": chat_content,
            "conversation_gap": conversation_gap,
        },
        max_output_tokens=_SUMMARY_MAX_TOKENS,
        log_prefix="daily_checkpoint",
        temperature=0.0,
    )
    if not parsed:
        return
    summary_text = str(parsed.get("summary") or "").strip()
    if not summary_text:
        return

    async with session_scope() as wdb:
        checkpoint = Message(
            conversation_id=conv_id,
            role="system",
            content=f"[📝 截至 {local_date_str} 的对话摘要]\n{summary_text}",
            subtype="daily_summary",
            summary_date=local_date_str,
        )
        wdb.add(checkpoint)
        await wdb.commit()
        # 检查点之前的视频不会再进读路径，清盘并改写历史行 part（与压缩检查点同一钩子）。
        await prune_videos_in_range(wdb, conv_id, hi=checkpoint.id)
        await wdb.commit()
    logger.info("daily_checkpoint: created summary", extra={"user_id": user_id, "date": local_date_str})


async def _collect_inputs(
    db: AsyncSession,
    user_id: int,
    utc_start: datetime,
    utc_end: datetime,
    local_date_str: str,
) -> tuple[int, str, str, dict[str, Any] | None] | None:
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

    # 最近任意类型的 checkpoint（daily_summary 或 compress_summary）——划定读边界；它之前的内容已被摘要。
    prev_checkpoint = (
        await db.execute(
            select(Message)
            .where(
                Message.conversation_id == main_conv.id,
                Message.id > main_conv.context_after_message_id,
                Message.subtype.in_(tuple(_CHECKPOINT_SUBTYPES)),
            )
            .order_by(Message.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    checkpoint_id = prev_checkpoint.id if prev_checkpoint else 0

    # checkpoint 之后必须至少有一条真 user/assistant turn；若 checkpoint 就是会话末条（无后续交互），没有新内容可摘要——checkpoint 本身已覆盖到那点。
    has_new_turns = (
        await db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == main_conv.id,
                Message.id > main_conv.context_after_message_id,
                Message.id > checkpoint_id,
                Message.role.in_(("user", "assistant")),
                real_turns,
            ),
        )
    ).scalar_one()
    if not has_new_turns:
        return

    # 从 checkpoint INCLUSIVE 读：summarisable filter 自然包含 compress_summary（其内容前滚）但排除 daily_summary（下面通过 prev_summary_block 单独喂入）；checkpoint 之前的消息——已由 checkpoint 摘要代表——被 id >= 边界排除。
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == main_conv.id,
                    Message.id > main_conv.context_after_message_id,
                    Message.id >= checkpoint_id,
                    real_turns,
                )
                .order_by(Message.id.asc()),
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return

    # 上一条 daily_summary 专门取出——给 prev_summary_block（更早的历史上下文）和间隔天数计算；若 checkpoint 本身就是 daily_summary 则直接复用。
    prev_daily = (
        prev_checkpoint
        if prev_checkpoint is not None and prev_checkpoint.subtype == "daily_summary"
        else (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == main_conv.id,
                    Message.id > main_conv.context_after_message_id,
                    Message.subtype == "daily_summary",
                )
                .order_by(Message.id.desc())
                .limit(1),
            )
        ).scalar_one_or_none()
    )
    prev_summary_text = prev_daily.content if prev_daily else ""
    prev_date = prev_daily.summary_date if prev_daily else None
    gap = _gap_days(prev_date, local_date_str)
    conversation_gap = (
        {
            "previous_summary_date": prev_date,
            "current_summary_date": local_date_str,
            "elapsed_local_days": gap,
        }
        if gap and gap > 1
        else None
    )
    return (main_conv.id, format_messages_compact(rows), prev_summary_text, conversation_gap)
