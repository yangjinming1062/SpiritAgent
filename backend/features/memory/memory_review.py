import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from infrastructure.ai.error_classifier import LlmCallBlockedError
from infrastructure.ai.prompt_engineer import call_llm_once
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import UserLlmConfig, resolve_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.text import parse_llm_json, utc_now
from runtime.memory_scope import MemoryScope, MemorySource

from features.conversation.evidence import latest_scope_message_id
from features.memory.contracts import MemoryConflictError, MemoryRecord
from features.memory.memory_learning import MemoryReviewContext, apply_memory_decisions, load_review_context
from features.memory.memory_policy import MemoryDecisions
from features.memory.prompts import MEMORY_POLICY, MEMORY_REVIEW_INSTRUCTIONS

logger = get_logger(__name__)

# 每个记忆作用域一把审阅锁；账户维护后丢弃锁与运行记录。
_REVIEW_LOCKS: dict[MemoryScope, asyncio.Lock] = {}


@dataclass(frozen=True)
class _ReviewBackoff:
    delay: float
    retry_at: float  # time.monotonic() 时刻


# 审阅失败后的重试退避：从 10 分钟起逐次翻倍，上限为 SETTINGS.memory_review_interval_seconds，成功后清除。
# 键为（作用域, 会话 ID），会话 ID 为 None 表示整域审阅；进程内状态，与审阅锁同样随用户维护边界丢弃。
_REVIEW_BACKOFF_START_SECONDS = 600.0
_REVIEW_BACKOFF: dict[tuple[MemoryScope, int | None], _ReviewBackoff] = {}

# 整域审阅的成功节流与失败退避共用作用域锁；会话级审阅仍按消息水位推进。
_LAST_MEMORY_REVIEW: dict[MemoryScope, float] = {}


async def review_memories(
    scope: MemoryScope,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    llm_config: UserLlmConfig | None = None,
    task: AiTaskKey = AiTaskKey.memory_review,
    before_call: Callable[[], Awaitable[None]] | None = None,
    reasoning_effort: str | None = None,
) -> bool:
    """锁内复核退避；整域审阅按成功间隔节流，会话审阅按消息水位推进。"""
    async with _REVIEW_LOCKS.setdefault(scope, asyncio.Lock()):
        previous = _LAST_MEMORY_REVIEW.get(scope)
        if (
            session_id is None
            and previous is not None
            and time.monotonic() - previous < SETTINGS.memory_review_interval_seconds
        ):
            return False
        if _review_backed_off(scope, session_id):
            return False
        try:
            await _review_batches(
                scope,
                session_id=session_id,
                through_message_id=through_message_id,
                llm_config=llm_config,
                task=task,
                before_call=before_call,
                reasoning_effort=reasoning_effort,
            )
        except (MemoryConflictError, LlmCallBlockedError):
            # 并发冲突和业务守卫的停止不计入失败退避。
            raise
        except Exception:
            _note_review_failure(scope, session_id)
            raise
        _REVIEW_BACKOFF.pop((scope, session_id), None)
        if session_id is None:
            _LAST_MEMORY_REVIEW[scope] = time.monotonic()
        return True


def invalidate_memory_review_locks(user_id: int) -> None:
    """丢弃指定用户的全部审阅锁条目与失败退避；仅在用户维护边界内调用（无在途审核）。"""
    for scope in [s for s in _REVIEW_LOCKS if s.user_id == user_id]:
        _REVIEW_LOCKS.pop(scope, None)
    for key in [k for k in _REVIEW_BACKOFF if k[0].user_id == user_id]:
        _REVIEW_BACKOFF.pop(key, None)
    for scope in [s for s in _LAST_MEMORY_REVIEW if s.user_id == user_id]:
        _LAST_MEMORY_REVIEW.pop(scope, None)


def _review_backed_off(scope: MemoryScope, session_id: int | None) -> bool:
    backoff = _REVIEW_BACKOFF.get((scope, session_id))
    return backoff is not None and time.monotonic() < backoff.retry_at


def _note_review_failure(scope: MemoryScope, session_id: int | None) -> None:
    previous = _REVIEW_BACKOFF.get((scope, session_id))
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
    before_call: Callable[[], Awaitable[None]] | None = None,
    reasoning_effort: str | None = None,
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
            json_output=True,
            before_call=before_call,
            reasoning_effort=reasoning_effort,
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


async def _review_batches(
    scope: MemoryScope,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    llm_config: UserLlmConfig | None = None,
    task: AiTaskKey = AiTaskKey.memory_review,
    before_call: Callable[[], Awaitable[None]] | None = None,
    reasoning_effort: str | None = None,
) -> None:
    started_at = utc_now()
    if through_message_id is None:
        async with session_scope() as db:
            through_message_id = await latest_scope_message_id(db, scope.user_id, scope.system_preset_id)
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
                llm_config = await resolve_user_llm_config(db, scope.user_id, task)
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
            before_call=before_call,
            reasoning_effort=reasoning_effort,
        )
