import asyncio
import time
from dataclasses import dataclass
from typing import Any

from components import SETTINGS, get_logger, parse_llm_json, session_scope, utc_now
from modules.conversation import Conversation, Message
from prompts.memory import MEMORY_POLICY, MEMORY_REVIEW_INSTRUCTIONS
from sqlalchemy import func, select

from services.contracts import MemoryScope, MemorySource
from services.infrastructure.llm import UserLlmConfig, call_llm_once, resolve_user_llm_config

from .memory_learning import (
    MemoryConflictError,
    MemoryRecord,
    MemoryReviewContext,
    apply_memory_decisions,
    load_review_context,
)
from .memory_policy import MemoryDecisions

logger = get_logger(__name__)

# 整批最多 24 项决策，证据引用与理由和推理共用输出预算。
_MEMORY_REVIEW_MAX_OUTPUT_TOKENS = 32768

# 每个记忆作用域一把审阅锁，保证同域审核串行；用户覆盖恢复/删除后经 invalidate 丢弃。
_REVIEW_LOCKS: dict[MemoryScope, asyncio.Lock] = {}


@dataclass(frozen=True)
class _ReviewBackoff:
    delay: float
    retry_at: float  # time.monotonic() 时刻


# 审阅失败后的重试退避：从 10 分钟起逐次翻倍，上限为 SETTINGS.memory_review_interval_seconds，成功后清除。
# 键为（作用域, 会话 ID），会话 ID 为 None 表示整域审阅；进程内状态，与审阅锁同样随用户维护边界丢弃。
_REVIEW_BACKOFF_START_SECONDS = 600.0
_REVIEW_BACKOFF: dict[tuple[MemoryScope, int | None], _ReviewBackoff] = {}


def invalidate_memory_review_locks(user_id: int) -> None:
    """丢弃指定用户的全部审阅锁条目与失败退避；仅在用户维护边界内调用（无在途审核）。"""
    for scope in [s for s in _REVIEW_LOCKS if s.user_id == user_id]:
        _REVIEW_LOCKS.pop(scope, None)
    for key in [k for k in _REVIEW_BACKOFF if k[0].user_id == user_id]:
        _REVIEW_BACKOFF.pop(key, None)


def memory_review_backed_off(scope: MemoryScope, session_id: int | None = None) -> bool:
    """该审阅范围近期失败、仍在退避期内；调度与回合后审阅据此跳过，避免同一批次反复付费重试。"""
    backoff = _REVIEW_BACKOFF.get((scope, session_id))
    return backoff is not None and time.monotonic() < backoff.retry_at


def _note_review_failure(scope: MemoryScope, session_id: int | None) -> None:
    previous = _REVIEW_BACKOFF.get((scope, session_id))
    if previous is not None and time.monotonic() < previous.retry_at:
        # 退避期内的再次失败（失败前已排队的审阅、夜间直接调用）不再翻倍：延迟按重试轮次增长，不随排队数量增长。
        return
    delay = _REVIEW_BACKOFF_START_SECONDS if previous is None else previous.delay * 2
    delay = min(delay, SETTINGS.memory_review_interval_seconds)
    _REVIEW_BACKOFF[(scope, session_id)] = _ReviewBackoff(delay, time.monotonic() + delay)
    logger.warning(
        "memory_review: failed, retries back off",
        extra={"scope": str(scope), "session_id": session_id, "retry_after_seconds": delay},
    )


async def assess_memory_changes(
    scope: MemoryScope,
    source: MemorySource,
    context: MemoryReviewContext,
    *,
    llm_config: UserLlmConfig,
    proposal: dict[str, Any] | None = None,
    advance_review: bool = False,
) -> list[MemoryRecord]:
    payload = context.payload()
    payload["system_preset_id"] = scope.system_preset_id
    payload["decision_schema"] = MemoryDecisions.model_json_schema()
    if proposal is not None:
        payload["untrusted_proposal"] = proposal
    for attempt in range(2):
        raw = await call_llm_once(
            llm_config,
            MEMORY_POLICY + "\n" + MEMORY_REVIEW_INSTRUCTIONS,
            payload,
            max_output_tokens=_MEMORY_REVIEW_MAX_OUTPUT_TOKENS,
            json_output=True,
        )
        try:
            parsed = MemoryDecisions.model_validate(parse_llm_json(raw))
            return await apply_memory_decisions(scope, source, context, parsed.decisions, advance_review=advance_review)
        except MemoryConflictError:
            raise
        except ValueError as exc:
            if attempt:
                raise
            payload["validation_feedback"] = str(exc)[:2000]
    raise RuntimeError("Memory review did not produce a valid decision batch")


async def review_memories(
    scope: MemoryScope,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    llm_config: UserLlmConfig | None = None,
) -> None:
    """审阅到截止消息；失败记入该范围的退避并向上抛出，成功清除退避。退避只由调用方查询，本函数不据此拒绝执行。"""
    try:
        await _review_batches(
            scope,
            session_id=session_id,
            through_message_id=through_message_id,
            llm_config=llm_config,
        )
    except MemoryConflictError:
        # 记忆在审阅期间被并发修改，属瞬时冲突：不计入失败，也不启动退避。
        raise
    except Exception:
        _note_review_failure(scope, session_id)
        raise
    _REVIEW_BACKOFF.pop((scope, session_id), None)


async def _review_batches(
    scope: MemoryScope,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    llm_config: UserLlmConfig | None = None,
) -> None:
    async with _REVIEW_LOCKS.setdefault(scope, asyncio.Lock()):
        started_at = utc_now()
        if through_message_id is None:
            async with session_scope() as db:
                through_message_id = (
                    await db.scalar(
                        select(func.max(Message.id))
                        .join(Conversation)
                        .where(
                            Conversation.user_id == scope.user_id,
                            Conversation.system_preset_id == scope.system_preset_id,
                            Message.discarded.is_(False),
                        ),
                    )
                    or 0
                )
        while True:
            async with session_scope() as db:
                context = await load_review_context(
                    db,
                    scope,
                    session_id=session_id,
                    through_message_id=through_message_id,
                    new_only=True,
                    reviewed_before=started_at,
                )
                if llm_config is None:
                    llm_config = await resolve_user_llm_config(db, scope.user_id)
            if not context.messages and (session_id is not None or not context.memories):
                return
            if not llm_config.is_configured:
                raise ValueError("Memory review requires an available LLM configuration")
            await assess_memory_changes(
                scope,
                MemorySource("reflection"),
                context,
                llm_config=llm_config,
                advance_review=True,
            )
