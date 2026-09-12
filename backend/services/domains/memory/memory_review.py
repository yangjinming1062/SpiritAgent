import asyncio
from typing import Any

from components import parse_llm_json, session_scope, utc_now
from modules.conversation import Conversation, Message
from sqlalchemy import func, select

from services.contracts.memory import MemoryScope, MemorySource
from services.infrastructure.llm import call_llm_once, resolve_user_llm_config

from .memory_learning import (
    MemoryConflictError,
    MemoryRecord,
    MemoryReviewContext,
    apply_memory_decisions,
    load_review_context,
)
from .memory_policy import MEMORY_POLICY, MEMORY_REVIEW_INSTRUCTIONS, MemoryDecisions

_REVIEW_LOCKS: dict[MemoryScope, asyncio.Lock] = {}


async def assess_memory_changes(
    scope: MemoryScope,
    source: MemorySource,
    context: MemoryReviewContext,
    *,
    llm_config: dict[str, Any],
    proposal: dict[str, Any] | None = None,
    advance_review: bool = False,
) -> list[MemoryRecord]:
    payload = context.payload()
    payload["system_preset_id"] = scope.system_preset_id
    payload["decision_schema"] = MemoryDecisions.model_json_schema()
    if proposal is not None:
        payload["untrusted_proposal"] = proposal
        payload["task"] = (
            "Independently assess this proposal against the original evidence. Reject unsupported generalizations. Return corrected decisions or an empty list."
        )
    for attempt in range(2):
        raw = await call_llm_once(
            llm_config,
            MEMORY_POLICY + "\n" + MEMORY_REVIEW_INSTRUCTIONS,
            payload,
            max_output_tokens=6000,
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
            payload["retry_instruction"] = (
                "Your last decision batch was rejected without changes. Correct the validation errors using only supplied evidence. Return valid decision JSON, or an empty decisions array."
            )
    raise RuntimeError("Memory review did not produce a valid decision batch")


async def review_memories(
    scope: MemoryScope,
    *,
    session_id: int | None = None,
    through_message_id: int | None = None,
    llm_config: dict[str, Any] | None = None,
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
                config = llm_config or await resolve_user_llm_config(db, scope.user_id)
            if not context.messages and (session_id is not None or not context.memories):
                return
            if not (config.get("api_key") and config.get("base_url") and config.get("model_name")):
                raise ValueError("Memory review requires an available LLM configuration")
            await assess_memory_changes(
                scope,
                MemorySource("reflection"),
                context,
                llm_config=config,
                advance_review=True,
            )
