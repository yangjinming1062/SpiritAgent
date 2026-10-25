"""夜间自主日记：决定、发布与阶段恢复。"""

from collections.abc import Awaitable, Callable
from datetime import date
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.companion.narrative_prompts import NARRATIVE_REVISION_TEXTS
from features.companion.narrative_quality import NarrativeQualityError, review_narrative
from features.journal.journal_service import get_diary_by_date, publish_diary
from features.journal.schemas import DIARY_BODY_MAX_CHARS, DiaryContent
from features.life.context import read_life_context_in_session
from features.memory.memory_narratives import backfill_diary_embeddings, load_companion_reflection
from features.posts.store import PostInteractions
from infrastructure.ai.prompt_engineer import LlmAttemptDiagnostic, call_llm_json
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.user_config import UserLlmConfig, load_user_llm_config
from infrastructure.config import SETTINGS
from infrastructure.constants import NIGHTLY_REASONING_EFFORT
from infrastructure.database import SESSION_LOCAL
from infrastructure.text import resolve_prompt_text, utc_now
from runtime.memory_scope import MemoryScope

from workflows.life_events import record_diary_published
from workflows.nightly.output_contracts import DIARY_DECISION, parse_diary_decision
from workflows.nightly.prompts import JOURNAL_DIARY_TEXTS, NIGHTLY_JSON_REPAIR_TEXTS
from workflows.nightly.stage_state import load_narrative_result, save_narrative_result
from workflows.nightly.window import in_nightly_window, require_nightly_window


async def project_today(
    user_id: int,
    target_date: date,
    *,
    log_id: int,
    messages: list[dict[str, str]],
    nightly_actions: list[dict[str, Any]],
    posts: PostInteractions,
    persona: dict[str, str],
    language: str,
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    user_timezone: str,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> bool:
    """True 已发布，False 正常跳过；模型调用或输出失败时抛出。"""
    previous_reflection = None
    life_context_payload: dict[str, Any] = {}
    async with SESSION_LOCAL() as db:
        previous = await load_narrative_result(db, log_id, "journal")
        existing = await get_diary_by_date(db, user_id, target_date)
        if previous is not None or existing is not None:
            result = previous if previous is not None else True
        elif not SETTINGS.diary_nightly_enabled:
            await save_narrative_result(db, log_id, "journal", False)
            await db.commit()
            return False
        else:
            result = None
            life_context_payload = (await read_life_context_in_session(db, user_id)).prompt_payload()
            reflection = await load_companion_reflection(db, MemoryScope(user_id, "companion"))
            if reflection is not None:
                previous_reflection = {
                    "local_date": reflection.local_date,
                    "content": reflection.content,
                }
    if result is not None:
        if result:
            await backfill_diary_embeddings(user_id)
        return result
    if not in_nightly_window(utc_now(), ZoneInfo(user_timezone)):
        return False
    composed = await _compose_diary(
        await load_user_llm_config(user_id, AiTaskKey.nightly),
        messages,
        target_date,
        nightly_actions,
        persona,
        posts.threads,
        language,
        contextual_memories,
        background_memories,
        previous_reflection,
        life_context_payload,
        before_call=partial(require_nightly_window, ZoneInfo(user_timezone)),
        diagnostics=diagnostics,
    )
    async with SESSION_LOCAL() as db:
        if not SETTINGS.diary_nightly_enabled:
            composed = False
        if isinstance(composed, DiaryContent):
            diary = await publish_diary(
                db, user_id, entry_date=target_date, content=composed, post_ids=posts.posted_ids
            )
            await record_diary_published(
                db,
                user_id,
                entry_date=target_date.isoformat(),
                title=diary.title,
                diary_id=diary.id,
            )
        await save_narrative_result(db, log_id, "journal", composed is not False)
        await db.commit()
    if composed is not False:
        await backfill_diary_embeddings(user_id)
    return composed is not False


async def _compose_diary(
    llm_cfg: UserLlmConfig,
    clean_messages: list[dict[str, str]],
    target_date: date,
    nightly_actions: list[dict[str, Any]],
    persona: dict[str, str],
    post_interactions: list[dict[str, Any]],
    language: str,
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    previous_reflection: dict[str, str | None] | None,
    life_context: dict[str, Any],
    *,
    before_call: Callable[[], Awaitable[None]] | None = None,
    diagnostics: list[LlmAttemptDiagnostic] | None = None,
) -> DiaryContent | Literal[False]:
    payload = {
        "local_date": target_date.isoformat(),
        "today_conversations": clean_messages,
        "nightly_autonomous_actions": nightly_actions,
        "post_interactions": post_interactions,
        "contextual_memories": contextual_memories,
        "background_memories": background_memories,
        "previous_reflection": previous_reflection,
        "life_context": life_context,
        "max_body_chars": DIARY_BODY_MAX_CHARS,
        "persona": persona,
    }
    instructions = resolve_prompt_text(JOURNAL_DIARY_TEXTS, language)
    creation_payload = payload
    for revision in range(2):
        diary = await call_llm_json(
            llm_cfg,
            instructions,
            creation_payload,
            reasoning_effort=NIGHTLY_REASONING_EFFORT,
            schema=DIARY_DECISION.json_schema(),
            schema_name="nightly_diary",
            parse_output=parse_diary_decision,
            repair_prompt=resolve_prompt_text(NIGHTLY_JSON_REPAIR_TEXTS, language),
            before_call=before_call,
            diagnostics=diagnostics,
        )
        if diary is False:
            return False
        review = await review_narrative(
            llm_cfg,
            payload,
            diary.model_dump(),
            language,
            before_call=before_call,
            diagnostics=diagnostics,
        )
        if not review.issues:
            return diary
        if revision:
            raise NarrativeQualityError(review)
        creation_payload = {
            **payload,
            "narrative_revision": {
                "draft": {"publish": True, **diary.model_dump()},
                "issues": review.model_dump()["issues"],
            },
        }
        instructions += resolve_prompt_text(NARRATIVE_REVISION_TEXTS, language)
    raise AssertionError("unreachable narrative revision state")
