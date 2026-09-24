"""夜间日记批处理：把刚结束的本地日投影到 diary_entries + 可选额外 0-2 条 moment。

调度侧在 ``run_nightly_pipeline`` 末尾调用 ``project_today``，日期由调用方传入避免两次计算漂移。
"""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from components import (
    DEFAULT_LANGUAGE,
    LLM_MAX_OUTPUT_TOKENS,
    SESSION_LOCAL,
    SETTINGS,
    get_logger,
    is_time_context_text,
    parse_llm_json,
    resolve_language,
    resolve_prompt_text,
    utc_now,
)
from modules.companion import (
    CompanionMoment,
    DiarySource,
    Persona,
)
from modules.conversation import Conversation, Message
from modules.settings import UserSetting
from prompts.nightly import JOURNAL_DIARY_TEXTS
from sqlalchemy import select

from services.domains.companion import load_persona_definition
from services.domains.conversation import UI_ONLY_SUBTYPES
from services.domains.journal import upsert_diary
from services.domains.memory import resolve_user_timezone
from services.infrastructure.llm import MissingLlmConfigError, UserLlmConfig, call_llm_once, resolve_user_llm_config

from .nightly_helpers import (
    get_local_day_utc_bounds,
    prefilter_messages_for_nightly,
)

logger = get_logger(__name__)


async def project_today(
    user_id: int,
    reference_utc: datetime | None = None,
    *,
    pre_messages: list[dict[str, str]] | None = None,
    llm_cfg: UserLlmConfig | None = None,
    nightly_actions: list[dict[str, Any]] | None = None,
    moment_interactions: list[dict[str, Any]] | None = None,
    language: str | None = None,
) -> bool | None:
    """夜间 upsert 当日（指 reference_utc 派生出的本地日）的日记，关联当日时刻。

    返回值语义：
    - ``True``：成功生成并落库夜间日记；
    - ``False``：因配置或数据条件被安全跳过；
    - ``None``：应生成日记，但 LLM/解析失败，且按原则七不写入伪造内容。
    """
    if not SETTINGS.diary_nightly_enabled:
        logger.info("journal_nightly: disabled by config", extra={"user_id": user_id})
        return False
    now_utc = reference_utc or utc_now()
    async with SESSION_LOCAL() as db:
        tz_str = await resolve_user_timezone(db, user_id)
        if not tz_str:
            logger.info(
                "journal_nightly: skipped, missing timezone",
                extra={"user_id": user_id},
            )
            return False
        try:
            utc_start, utc_end, _, local_date_str = get_local_day_utc_bounds(
                now_utc,
                tz_str,
            )
        except (ZoneInfoNotFoundError, ValueError):
            return False
        target_date = date.fromisoformat(local_date_str)
        if pre_messages is None:
            msgs = (
                (
                    await db.execute(
                        select(Message)
                        .join(Conversation, Message.conversation_id == Conversation.id)
                        .where(
                            Conversation.user_id == user_id,
                            Conversation.kind.in_(("special", "standard")),
                            Conversation.system_preset_id == "companion",
                            Conversation.is_automation.is_(False),
                            Message.id > Conversation.context_after_message_id,
                            Message.role.in_(("user", "assistant")),
                            Message.subtype.is_(None) | Message.subtype.notin_(tuple(UI_ONLY_SUBTYPES)),
                            Message.created_at >= utc_start,
                            Message.created_at < utc_end,
                        )
                        .order_by(Message.id.asc()),
                    )
                )
                .scalars()
                .all()
            )
            clean = prefilter_messages_for_nightly(msgs, user_tz=tz_str)
        else:
            clean = pre_messages
        if language is None:
            lang_val = (
                await db.execute(
                    select(UserSetting.setting_value).where(
                        UserSetting.user_id == user_id,
                        UserSetting.setting_key == "language",
                    ),
                )
            ).scalar()
            user_language = resolve_language(lang_val)
        else:
            user_language = resolve_language(language)
        if llm_cfg is None:
            llm_cfg = await resolve_user_llm_config(db, user_id)

        persona = await db.scalar(select(Persona).where(Persona.user_id == user_id))
        definition = load_persona_definition(persona) if persona is not None else {}
        persona_definition = {
            key: definition[key]
            for key in ("name", "personality", "speaking_style", "relationship")
            if definition.get(key)
        }

        today_moment_ids = (
            (
                await db.execute(
                    select(CompanionMoment.id).where(
                        CompanionMoment.user_id == user_id,
                        CompanionMoment.occurred_at >= utc_start,
                        CompanionMoment.occurred_at < utc_end,
                    ),
                )
            )
            .scalars()
            .all()
        )

    linked_nightly_moment_ids = [
        str(item["moment_id"]) for item in (nightly_actions or []) if isinstance(item, dict) and item.get("moment_id")
    ]
    today_moment_ids = list(
        dict.fromkeys([*today_moment_ids, *linked_nightly_moment_ids]),
    )

    action_facts = nightly_actions or []
    if (
        not any(m["role"] == "user" and not is_time_context_text(m["content"]) for m in clean)
        and not action_facts
        and not moment_interactions
    ):
        logger.info(
            "journal_nightly: no user messages or autonomous actions today",
            extra={"user_id": user_id},
        )
        return False

    title, body = await _compose_diary(
        user_id,
        llm_cfg,
        clean,
        target_date,
        action_facts,
        persona_definition,
        moment_interactions=moment_interactions,
        language=user_language,
    )
    if body is None:
        logger.warning(
            "journal_nightly: skipped persisting nightly diary due to compose failure",
            extra={"user_id": user_id},
        )
        return None
    async with SESSION_LOCAL() as db:
        await upsert_diary(
            db,
            user_id,
            entry_date=target_date,
            title=title,
            body=body,
            source=DiarySource.NIGHTLY.value,
            moment_ids=list(today_moment_ids),
        )
    return True


async def _compose_diary(
    user_id: int,
    llm_cfg: UserLlmConfig | None,
    clean_messages: list[dict[str, str]],
    target_date: date,
    nightly_actions: list[dict[str, Any]],
    persona: dict[str, Any],
    moment_interactions: list[dict[str, Any]] | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str, str | None]:
    if not (llm_cfg and llm_cfg.api_key and llm_cfg.base_url and llm_cfg.model_name):
        logger.warning(
            "journal_nightly: missing llm config",
            extra={"user_id": user_id},
        )
        return "", None
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
    except MissingLlmConfigError as exc:
        logger.warning(
            "journal_nightly: missing llm config",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return "", None
    except Exception:
        logger.warning("journal_nightly: LLM compose failed", exc_info=True)
        return "", None
    parsed = parse_llm_json(raw) or {}
    title = ""
    body = ""
    if isinstance(parsed, dict):
        title = parsed.get("title")
        body = parsed.get("body")
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
        return "", None
    return title.strip(), body.strip()
