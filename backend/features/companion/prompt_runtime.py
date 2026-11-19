from typing import Any, NamedTuple

from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.prompt_engineer import call_llm_once
from infrastructure.ai.response_validation import IncompleteLlmResponseError
from infrastructure.ai.user_config import UserLlmConfig
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.search import QueryEmbedding
from infrastructure.text import format_local_iso, parse_llm_json, resolve_language, utc_now
from pydantic import BaseModel
from runtime.memory_scope import MemoryScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from features.companion.models import Persona
from features.companion.persona_service import build_system_prompt_extras
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting
from features.conversation.context_window import delivered_life_excerpts
from features.life.context import contact_excerpt_sources, read_life_context_in_session
from features.life.schemas import LifeContext
from features.memory.memory_bootstrap import build_user_profile_extras
from features.memory.memory_format import (
    format_background_memory_block,
    format_companion_reflection_block,
    format_memories_block,
    format_proactive_memory_block,
)
from features.memory.memory_retrieval import embed_memory_text, retrieve_proactive_memories

logger = get_logger(__name__)


class DeliveredLifeHistory(NamedTuple):
    deliveries: list[dict[str, Any]]
    source_excerpts: dict[int, dict[str, str]]


async def delivered_life_history(db: AsyncSession, user_id: int, conversation_id: int | None) -> DeliveredLifeHistory:
    messages = await delivered_life_excerpts(db, user_id, conversation_id)
    sources = await contact_excerpt_sources(db, user_id, {message.excerpt_id for message in messages})
    deliveries = [
        {
            "delivered_at": message.delivered_at.isoformat(),
            "delivered_bubbles": message.bubbles,
            "source_excerpt_id": str(message.excerpt_id),
        }
        for message in messages
        if message.excerpt_id in sources
    ]
    return DeliveredLifeHistory(deliveries, sources)


class CompanionMemoryContext(BaseModel):
    user_profile: str = ""
    relationship: str = ""
    background: str = ""
    recalled: str = ""


async def get_companion_memory_context(
    db: AsyncSession,
    user_id: int,
    language: str,
    *,
    query: str = "",
    query_embedding: QueryEmbedding | None = None,
    include_recent: bool = True,
) -> CompanionMemoryContext:
    """读取当前有效的用户资料、相处理解和相关记忆。"""
    scope = MemoryScope(user_id, "companion")
    recalled = [
        item
        for item in await retrieve_proactive_memories(db, scope, query, query_embedding=query_embedding, limit=4)
        if item.kind != "reflection"
    ]
    recent = format_proactive_memory_block(recalled, language=language)
    if not recent and include_recent:
        recent = await format_memories_block(db, scope, language=language, limit=4)
    return CompanionMemoryContext(
        user_profile=await build_user_profile_extras(db, scope, language=language),
        relationship=await format_companion_reflection_block(db, scope, language=language),
        background=await format_background_memory_block(db, scope, language=language),
        recalled=recent,
    )


class CompanionPromptContext(BaseModel):
    """供心情、表达和动态使用的人设、心情与记忆；不注入视觉生成资料。"""

    language: str
    # 带时区偏移的用户本地时间；未设置时区时按 UTC。
    current_time: str
    persona_extras: str
    current_mood: str
    memory: CompanionMemoryContext = CompanionMemoryContext()
    life_context: LifeContext
    # 宿主模式不进入角色提示词，实际画面由对话流程按需装配。


class PromptOutcome(NamedTuple):
    """run_prompt_json 的结果：成功时 parsed 有值，失败时以 reason 区分错误类型。"""

    parsed: dict | None
    reason: str | None


async def load_companion_prompt_context(
    user_id: int, *, include_life_topics: bool = False, memory_query: str = ""
) -> CompanionPromptContext | None:
    """返回用于提示词的人设与记忆快照；人设未就绪时返回 None。从 user_settings 解析 language，驱动 persona_extras 的双语渲染。"""
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return None
        language = resolve_language(await get_user_setting(db, user_id, "language"))
        life_context = await read_life_context_in_session(
            db, user_id, growth_query=memory_query, include_topic_heads=include_life_topics
        )
        query = memory_query or " ".join(
            [life_context.state.current_activity, *(p.title for p in life_context.active_pursuits)]
        )
        local_time = format_local_iso(utc_now(), await resolve_user_timezone(db, user_id)) or ""
        definition = build_system_prompt_extras(persona, language=language)
    embedding = await embed_memory_text(user_id, query) if len(query.strip()) > 1 else None
    async with SESSION_LOCAL() as db:
        memory = await get_companion_memory_context(db, user_id, language, query=query, query_embedding=embedding)
        return CompanionPromptContext(
            language=language,
            current_time=local_time,
            persona_extras=definition,
            current_mood=life_context.state.mood or "",
            memory=memory,
            life_context=life_context,
        )


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
            f"{log_prefix}: unparseable LLM response", extra={"user_id": user_id, "raw_chars": len(raw or "")}
        )
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
