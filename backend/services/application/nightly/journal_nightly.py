"""夜间日记投影：把刚结束的本地日写成用户可见日记，并关联当日片刻。

``run_nightly_pipeline`` 已完成时区、素材门控与上下文收集，这里只负责撰写与落库。
"""

from datetime import date
from typing import Any

from components import LLM_MAX_OUTPUT_TOKENS, SESSION_LOCAL, SETTINGS, get_logger, parse_llm_json, resolve_prompt_text
from modules.companion import DiarySource
from prompts.nightly import JOURNAL_DIARY_TEXTS

from services.domains.journal import MomentInteractions, upsert_diary
from services.infrastructure.llm import UserLlmConfig, call_llm_once

logger = get_logger(__name__)


async def project_today(
    user_id: int,
    target_date: date,
    *,
    messages: list[dict[str, str]],
    llm_cfg: UserLlmConfig,
    nightly_actions: list[dict[str, Any]],
    moments: MomentInteractions,
    persona: dict[str, str],
    language: str,
) -> bool | None:
    """upsert target_date 的夜间日记。

    返回值语义：
    - ``True``：成功生成并落库夜间日记；
    - ``False``：夜间日记已由配置关闭；
    - ``None``：应生成日记，但 LLM/解析失败，且按原则七不写入伪造内容。
    """
    if not SETTINGS.diary_nightly_enabled:
        logger.info("journal_nightly: disabled by config", extra={"user_id": user_id})
        return False
    composed = await _compose_diary(
        user_id,
        llm_cfg,
        messages,
        target_date,
        nightly_actions,
        persona,
        moments.threads,
        language,
    )
    if composed is None:
        logger.warning(
            "journal_nightly: skipped persisting nightly diary due to compose failure",
            extra={"user_id": user_id},
        )
        return None
    title, body = composed
    # 夜间动作产生的片刻发布于次日凌晨，不在当日窗口内，需显式关联。
    action_moment_ids = [str(item["moment_id"]) for item in nightly_actions if item.get("moment_id")]
    async with SESSION_LOCAL() as db:
        await upsert_diary(
            db,
            user_id,
            entry_date=target_date,
            title=title,
            body=body,
            source=DiarySource.NIGHTLY.value,
            moment_ids=list(dict.fromkeys([*moments.posted_ids, *action_moment_ids])),
        )
    return True


async def _compose_diary(
    user_id: int,
    llm_cfg: UserLlmConfig,
    clean_messages: list[dict[str, str]],
    target_date: date,
    nightly_actions: list[dict[str, Any]],
    persona: dict[str, str],
    moment_interactions: list[dict[str, Any]],
    language: str,
) -> tuple[str, str] | None:
    payload = {
        "local_date": target_date.isoformat(),
        "today_conversations": clean_messages[-40:],
        "nightly_autonomous_actions": nightly_actions,
        **({"moment_interactions": moment_interactions} if moment_interactions else {}),
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
        or len(body) > 2000
        or not body.strip()
    ):
        logger.warning(
            "journal_nightly: invalid diary fields from compose",
            extra={"user_id": user_id},
        )
        return None
    return title.strip(), body.strip()
