from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.prompt_engineer import IncompleteLlmResponseError, call_llm_once
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.text import format_local_iso, parse_llm_json, resolve_language, resolve_prompt_text, utc_now
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.models import Persona
from features.companion.persona_service import load_persona_definition, render_extras
from features.companion.proactive_runtime import PresentationSnapshot, get_presentation_snapshot
from features.companion.prompts import PRESENTATION_CONTEXTS
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting
from features.memory.memory_format import format_memories_block

logger = get_logger(__name__)

ExpressionCatalogReader = Callable[[AsyncSession, int], Awaitable[list[dict[str, Any]]]]
_expression_catalog_reader: ExpressionCatalogReader | None = None


def set_expression_catalog_reader(reader: ExpressionCatalogReader) -> None:
    global _expression_catalog_reader
    _expression_catalog_reader = reader


class CompanionPromptContext(BaseModel):
    """供心情、表达和动态使用的人设、心情与记忆；不注入视觉生成资料。"""

    language: str
    # 带时区偏移的用户本地时间；未设置时区时按 UTC。
    current_time: str
    persona_extras: str
    current_mood: str
    memories_block: str
    presentation: PresentationSnapshot | None


def presentation_prompt(snapshot: PresentationSnapshot | None, *, language: str) -> str:
    if snapshot is None:
        return ""
    return resolve_prompt_text(PRESENTATION_CONTEXTS[snapshot.mode], language)


def build_presentation_prompt(user_id: int, *, language: str) -> str:
    return presentation_prompt(get_presentation_snapshot(user_id), language=language)


class PromptOutcome(NamedTuple):
    """run_prompt_json 的结果：成功时 parsed 有值，失败时以 reason 区分错误类型。"""

    parsed: dict | None
    reason: str | None


async def load_companion_prompt_context(user_id: int) -> CompanionPromptContext | None:
    """返回用于提示词的人设与记忆快照；人设未就绪时返回 None。从 user_settings 解析 language，驱动 persona_extras 的双语渲染。"""
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return None
        language = resolve_language(await get_user_setting(db, user_id, "language"))
        return CompanionPromptContext(
            language=language,
            current_time=format_local_iso(utc_now(), await resolve_user_timezone(db, user_id)) or "",
            persona_extras=render_extras(load_persona_definition(persona), language=language),
            current_mood=persona.current_mood or "",
            memories_block=await format_memories_block(db, MemoryScope(user_id, "companion"), language=language),
            presentation=get_presentation_snapshot(user_id),
        )


async def load_available_expression_actions(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """返回当前激活包中 LLM 可点播的表达动作清单；每项含 action_id/name/use_when 等。系统产品槽位不进清单，动态动作即表达能力。"""
    if _expression_catalog_reader is None:
        raise RuntimeError("Expression catalog reader has not been registered")
    return await _expression_catalog_reader(db, user_id)


async def run_prompt_json(
    user_id: int,
    llm_config: UserLlmConfig,
    instructions: str,
    payload: dict[str, Any],
    *,
    max_output_tokens: int | None = None,
    log_prefix: str,
    temperature: float = 0.2,
) -> PromptOutcome:
    """执行一次结构化伙伴推理；静态规则放 instructions，运行时数据作为 JSON 输入。"""
    if not llm_config.is_configured:
        return PromptOutcome(parsed=None, reason="llm_error")
    try:
        raw = await call_llm_once(
            llm_config,
            instructions,
            payload,
            max_output_tokens=max_output_tokens,
            json_output=True,
            temperature=temperature,
        )
    except IncompleteLlmResponseError as exc:
        logger.info(f"{log_prefix}: incomplete response", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="incomplete_response")
    except (TimeoutError, LLMRuntimeError, RuntimeError) as exc:
        logger.warning(f"{log_prefix}: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="llm_error")

    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        # 输出基于用户对话生成，常规日志不记原文；开启 LLM 调试日志后可按响应 ID 对照。
        logger.warning(
            f"{log_prefix}: unparseable LLM response",
            extra={"user_id": user_id, "raw_chars": len(raw or "")},
        )
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
