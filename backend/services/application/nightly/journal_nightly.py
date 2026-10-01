"""按目标本地日生成用户可见日记并关联动态；素材由夜间编排提供。"""

from datetime import date
from typing import Any

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, SETTINGS, get_logger, parse_llm_json, resolve_prompt_text
from modules.companion import DiarySource
from prompts.nightly import JOURNAL_DIARY_TEXTS

from services.domains.journal import diary_append_capacity, upsert_diary
from services.domains.posts import PostInteractions
from services.infrastructure.llm import UserLlmConfig, call_llm_once

logger = get_logger(__name__)

# 剩余容量太小写不出完整补记时不调用模型。
_MIN_APPEND_CHARS = 80


async def project_today(
    user_id: int,
    target_date: date,
    *,
    messages: list[dict[str, str]],
    llm_cfg: UserLlmConfig,
    nightly_actions: list[dict[str, Any]],
    posts: PostInteractions,
    persona: dict[str, str],
    language: str,
) -> bool | None:
    """生成或追加目标日的日记；True 表示已保存，False 表示无需生成，None 表示生成失败。"""
    if not SETTINGS.diary_nightly_enabled:
        logger.info("journal_nightly: disabled by config", extra={"user_id": user_id})
        return False
    async with SESSION_LOCAL() as db:
        existing_entry, max_body_chars = await diary_append_capacity(
            db,
            user_id,
            target_date,
            source=DiarySource.NIGHTLY.value,
        )
    # 同日日记已写满时不再补记，避免模型写出注定无法保存的内容。
    if max_body_chars < _MIN_APPEND_CHARS:
        logger.info("journal_nightly: same-day diary is full", extra={"user_id": user_id})
        return False
    composed = await _compose_diary(
        user_id,
        llm_cfg,
        messages,
        target_date,
        nightly_actions,
        persona,
        posts.threads,
        language,
        existing_entry=existing_entry,
        max_body_chars=max_body_chars,
    )
    if composed is None:
        logger.warning(
            "journal_nightly: skipped persisting nightly diary due to compose failure",
            extra={"user_id": user_id},
        )
        return None
    title, body = composed
    async with SESSION_LOCAL() as db:
        await upsert_diary(
            db,
            user_id,
            entry_date=target_date,
            title=title,
            body=body,
            source=DiarySource.NIGHTLY.value,
            post_ids=posts.posted_ids,
        )
    return True


async def _compose_diary(
    user_id: int,
    llm_cfg: UserLlmConfig,
    clean_messages: list[dict[str, str]],
    target_date: date,
    nightly_actions: list[dict[str, Any]],
    persona: dict[str, str],
    post_interactions: list[dict[str, Any]],
    language: str,
    *,
    existing_entry: str,
    max_body_chars: int,
) -> tuple[str, str] | None:
    payload = {
        "local_date": target_date.isoformat(),
        "today_conversations": clean_messages[-40:],
        "nightly_autonomous_actions": nightly_actions,
        **({"post_interactions": post_interactions} if post_interactions else {}),
        **({"existing_entry": existing_entry} if existing_entry else {}),
        "max_body_chars": max_body_chars,
        "persona": persona,
        "language": language,
    }
    try:
        raw = await call_llm_once(
            llm_cfg,
            resolve_prompt_text(JOURNAL_DIARY_TEXTS, language),
            payload,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            json_output=True,
        )
    except Exception:
        logger.warning("journal_nightly: LLM compose failed", extra={"user_id": user_id}, exc_info=True)
        return None
    parsed = parse_llm_json(raw)
    title = parsed.get("title") if isinstance(parsed, dict) else None
    body = parsed.get("body") if isinstance(parsed, dict) else None
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or len(title) > 128
        or len(body.strip()) > max_body_chars
        or not body.strip()
    ):
        logger.warning(
            "journal_nightly: invalid diary fields from compose",
            extra={"user_id": user_id},
        )
        return None
    return title.strip(), body.strip()
