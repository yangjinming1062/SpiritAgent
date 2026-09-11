from typing import Any, NamedTuple

from components import DEFAULT_LANGUAGE, SESSION_LOCAL, get_logger, parse_llm_json, resolve_language, safe_json_loads
from modules.companion import Persona
from modules.settings import UserSetting
from pydantic import BaseModel
from sqlalchemy import select

from ..llm import LLMRuntimeError, UserLlmConfig, build_responses_kwargs, call_with_retry, client_for_config
from .emotions import BUILTIN_EMOTIONS
from .memory_format import format_memories_block
from .mesh2d import DEFAULT_ACTIONS, NON_LLM_ACTIONS
from .outfit_service import build_outfit_extras
from .persona_service import render_extras
from .pipeline import get_active_model

logger = get_logger(__name__)

# 应用状态机与用户直接交互使用的 clip 不得被自主情境推理主动点播。
_NON_AUTONOMOUS_CLIP_KEYS = frozenset({"idle", "emotional", "interacting", "poke", "drag"})


class CompanionPromptContext(BaseModel):
    """人设、心情、记忆、着装与具身能力快照，每次提示词只加载一次，避免调用方重复查询。"""

    persona_name: str
    persona_extras: str
    current_mood: str
    outfit_block: str
    memories_block: str
    allowed_emotions: set[str]
    available_actions: list[str]


class PromptOutcome(NamedTuple):
    """run_prompt_json 的结果：成功时 parsed 有值，失败时以 reason 区分错误类型。"""

    parsed: dict | None
    reason: str | None


async def load_companion_prompt_context(user_id: int) -> CompanionPromptContext | None:
    """返回用于提示词的人设与记忆快照；人设未就绪时返回 None。

    从 user_settings 内部解析 language（caller 不必传），驱动 outfit_block 与 persona_extras 的双语渲染。
    """
    async with SESSION_LOCAL() as db:
        persona = (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if persona is None or not persona.is_complete:
            return None
        language_setting = (
            await db.execute(
                select(UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key == "language",
                ),
            )
        ).scalar()
        language = resolve_language(language_setting or DEFAULT_LANGUAGE)
        definition = safe_json_loads(persona.definition_json or "{}", default={})
        persona_name = str(definition.get("name") or "桌面伙伴").strip()
        available_actions: list[str] = []
        active_model = await get_active_model(db, user_id)
        if active_model is not None:
            clip_map = safe_json_loads(active_model.clip_map_json or "{}", default={})
            if isinstance(clip_map, dict):
                available_actions = sorted(set(clip_map) - _NON_AUTONOMOUS_CLIP_KEYS)
        if not available_actions:
            available_actions = sorted(set(DEFAULT_ACTIONS) - NON_LLM_ACTIONS)
        return CompanionPromptContext(
            persona_name=persona_name,
            persona_extras=render_extras(definition, language=language),
            current_mood=persona.current_mood or "",
            outfit_block=await build_outfit_extras(db, user_id, language=language),
            memories_block=await format_memories_block(db, user_id),
            allowed_emotions=set(BUILTIN_EMOTIONS),
            available_actions=available_actions,
        )


async def run_prompt_json(
    user_id: int,
    llm_config: UserLlmConfig | dict[str, Any],
    template: str,
    prompt_args: dict[str, Any],
    *,
    max_output_tokens: int,
    log_prefix: str,
) -> PromptOutcome:
    """一次性的人设 JSON 提示词调用；失败时用 reason 区分模型/传输错误与响应无法解析。"""
    model_name = (
        llm_config.model_name
        if isinstance(llm_config, UserLlmConfig)
        else (llm_config.get("model_name") if isinstance(llm_config, dict) else "")
    )
    if not model_name:
        return PromptOutcome(parsed=None, reason="llm_error")

    prompt = template.format(**prompt_args)

    try:
        client = client_for_config(llm_config)
        request = build_responses_kwargs(
            model=model_name,
            instructions="",
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            temperature=0.7,
            max_output_tokens=max_output_tokens,
        )
        response = await call_with_retry(client, **request)
    except (TimeoutError, LLMRuntimeError) as exc:
        logger.warning(f"{log_prefix}: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="llm_error")

    raw = response.output_text
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning(f"{log_prefix}: unparseable LLM response", extra={"user_id": user_id, "raw": (raw or "")[:200]})
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
