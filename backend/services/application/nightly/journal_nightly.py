"""夜间自主日记：决定、发布与阶段恢复。"""

from datetime import date
from typing import Any, Literal
from zoneinfo import ZoneInfo

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, SETTINGS, parse_llm_json, resolve_prompt_text, utc_now
from modules.companion import DIARY_BODY_MAX_CHARS, DiaryContent
from prompts.nightly import JOURNAL_DIARY_TEXTS
from pydantic import ValidationError

from services.contracts import MemoryScope
from services.domains.journal import get_diary_by_date, publish_diary
from services.domains.memory import backfill_diary_embeddings, load_companion_reflection, narrative_date
from services.domains.posts import PostInteractions
from services.infrastructure.llm import UserLlmConfig, call_llm_once

from .stage_state import load_narrative_result, save_narrative_result
from .window import in_nightly_window


async def project_today(
    user_id: int,
    target_date: date,
    *,
    log_id: int,
    messages: list[dict[str, str]],
    llm_cfg: UserLlmConfig,
    nightly_actions: list[dict[str, Any]],
    posts: PostInteractions,
    persona: dict[str, str],
    language: str,
    contextual_memories: dict[str, str],
    background_memories: dict[str, str],
    user_timezone: str,
) -> bool:
    """True 已发布，False 正常跳过；模型调用或输出失败时抛出。"""
    previous_reflection = None
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
            reflection = await load_companion_reflection(db, MemoryScope(user_id, "companion"))
            if reflection is not None:
                previous_reflection = {
                    "local_date": narrative_date(reflection.context, reflection.source_refs),
                    "content": reflection.content,
                }
    if result is not None:
        if result:
            await backfill_diary_embeddings(user_id)
        return result
    if not in_nightly_window(utc_now(), ZoneInfo(user_timezone)):
        return False
    composed = await _compose_diary(
        llm_cfg,
        messages,
        target_date,
        nightly_actions,
        persona,
        posts.threads,
        language,
        contextual_memories,
        background_memories,
        previous_reflection,
    )
    async with SESSION_LOCAL() as db:
        if not SETTINGS.diary_nightly_enabled:
            composed = False
        if isinstance(composed, DiaryContent):
            await publish_diary(db, user_id, entry_date=target_date, content=composed, post_ids=posts.posted_ids)
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
) -> DiaryContent | Literal[False]:
    payload = {
        "local_date": target_date.isoformat(),
        "today_conversations": clean_messages,
        "nightly_autonomous_actions": nightly_actions,
        "post_interactions": post_interactions,
        "contextual_memories": contextual_memories,
        "background_memories": background_memories,
        "previous_reflection": previous_reflection,
        "max_body_chars": DIARY_BODY_MAX_CHARS,
        "persona": persona,
        "language": language,
    }
    raw = await call_llm_once(
        llm_cfg,
        resolve_prompt_text(JOURNAL_DIARY_TEXTS, language),
        payload,
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        json_output=True,
    )
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("publish"), bool):
        raise ValueError("Expected an explicit publish decision")
    if parsed["publish"] is False:
        if set(parsed) != {"publish"}:
            raise ValueError("Declined diary must contain only publish")
        return False
    try:
        return DiaryContent.model_validate({key: value for key, value in parsed.items() if key != "publish"})
    except ValidationError as exc:
        # 错误文本进入夜间日志与管理页：不带模型输出原文，只列日记自有字段名，模型自取的字段名以固定措辞概括。
        failed = {str(error["loc"][0]) if error["loc"] else "" for error in exc.errors(include_input=False)}
        fields = sorted(name for name in failed if name in DiaryContent.model_fields)
        if len(fields) < len(failed):
            fields.append("unexpected fields")
        raise ValueError(f"invalid diary fields: {', '.join(fields)}") from None
