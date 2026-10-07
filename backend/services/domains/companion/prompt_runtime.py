from typing import Any, NamedTuple

from components import (
    SESSION_LOCAL,
    format_local_iso,
    get_logger,
    parse_llm_json,
    resolve_language,
    utc_now,
)
from modules.companion import Persona
from modules.settings import get_user_setting, resolve_user_timezone
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope
from services.domains.actions import action_prompt_entry, get_active_pack, is_expression_action, list_pack_actions
from services.domains.memory import format_memories_block
from services.infrastructure.llm import (
    IncompleteLlmResponseError,
    LLMRuntimeError,
    UserLlmConfig,
    call_llm_once,
)

from .persona_service import load_persona_definition, render_extras

logger = get_logger(__name__)


class CompanionPromptContext(BaseModel):
    """供心情、表达和动态使用的人设、心情与记忆；不注入视觉生成资料。"""

    language: str
    # 带时区偏移的用户本地时间；未设置时区时按 UTC。
    current_time: str
    persona_extras: str
    current_mood: str
    memories_block: str


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
        )


async def load_available_expression_actions(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    """返回当前激活包中 LLM 可点播的表达动作清单；每项含 action_id/name/use_when 等。系统产品槽位不进清单，动态动作即表达能力。"""
    pack = await get_active_pack(db, user_id)
    if pack is None:
        return []
    available_actions: list[dict[str, Any]] = []
    for row in await list_pack_actions(db, pack.id, enabled_only=True):
        if is_expression_action(row):
            available_actions.append(action_prompt_entry(row))
    available_actions.sort(key=lambda item: item["action_id"])
    return available_actions


async def run_prompt_json(
    user_id: int,
    llm_config: UserLlmConfig,
    instructions: str,
    payload: dict[str, Any],
    *,
    max_output_tokens: int,
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
