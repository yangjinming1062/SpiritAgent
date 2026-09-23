import json
from typing import Any, NamedTuple

from components import DEFAULT_LANGUAGE, SESSION_LOCAL, get_logger, parse_llm_json, resolve_language, safe_json_loads
from modules.companion import Persona
from modules.settings import UserSetting
from pydantic import BaseModel
from sqlalchemy import select

from services.contracts import MemoryScope
from services.domains.actions import get_active_pack, list_pack_actions
from services.domains.memory import format_memories_block
from services.infrastructure.llm import (
    LLMRuntimeError,
    ServiceType,
    UserLlmConfig,
    build_responses_kwargs,
    call_with_retry,
    client_for_config,
    try_resolve,
)

from .appearance import build_outfit_extras
from .character_card import load_character_snapshot, render_character_profile
from .persona_service import render_extras

logger = get_logger(__name__)


class CompanionPromptContext(BaseModel):
    """人设、心情、记忆、着装与具身能力快照，每次提示词只加载一次，避免调用方重复查询。"""

    language: str
    persona_extras: str
    current_mood: str
    outfit_block: str
    memories_block: str
    # 每项含 action_id/name/use_when 等；系统产品槽位不进清单。
    available_actions: list[dict[str, Any]]


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
        # LLM 可点播的表达动作来自当前激活包；系统产品槽位不进清单，动态动作即表达能力。
        pack = await get_active_pack(db, user_id)
        available_actions: list[dict[str, Any]] = []
        if pack is not None:
            for row in await list_pack_actions(db, pack.id, enabled_only=True):
                if row.status != "succeeded" or not row.video_path:
                    continue
                if row.system_slot:
                    continue
                duration_ms = row.actual_duration_ms or int((row.target_duration_seconds or 0) * 1000)
                available_actions.append(
                    {
                        "action_id": row.id,
                        "name": row.name or row.key,
                        "motion_description": row.motion_description,
                        "use_when": safe_json_loads(row.use_when or "[]", default=[]),
                        "avoid_when": safe_json_loads(row.avoid_when or "[]", default=[]),
                        "kind": row.kind,
                        "duration_seconds": round(duration_ms / 1000, 3) if duration_ms else 0.0,
                    },
                )
        available_actions.sort(key=lambda item: item["action_id"])
        return CompanionPromptContext(
            language=language,
            persona_extras=render_extras(definition, language=language)
            + "\n"
            + render_character_profile(await load_character_snapshot(db, user_id)),
            current_mood=persona.current_mood or "",
            outfit_block=await build_outfit_extras(db, user_id, language=language),
            memories_block=await format_memories_block(db, MemoryScope(user_id, "companion")),
            available_actions=available_actions,
        )


async def run_prompt_json(
    user_id: int,
    llm_config: UserLlmConfig | dict[str, Any],
    instructions: str,
    payload: dict[str, Any],
    *,
    max_output_tokens: int,
    log_prefix: str,
    temperature: float = 0.2,
) -> PromptOutcome:
    """执行一次结构化伙伴推理；静态规则放 instructions，运行时数据作为 JSON 输入。"""
    model_name = (
        llm_config.model_name
        if isinstance(llm_config, UserLlmConfig)
        else (llm_config.get("model_name") if isinstance(llm_config, dict) else "")
    )
    if not model_name:
        return PromptOutcome(parsed=None, reason="llm_error")

    try:
        client = client_for_config(llm_config)
        provider_cls = try_resolve(ServiceType.llm, llm_config.get("provider_name") or "")
        request = build_responses_kwargs(
            model=model_name,
            instructions=instructions,
            input_items=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}],
                },
            ],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            text={"format": {"type": "json_object"}} if getattr(provider_cls, "supports_json_object", False) else None,
        )
        response = await call_with_retry(client, **request)
    except (TimeoutError, LLMRuntimeError) as exc:
        logger.warning(f"{log_prefix}: LLM call failed", extra={"user_id": user_id, "error": str(exc)})
        return PromptOutcome(parsed=None, reason="llm_error")

    if response.status != "completed":
        logger.info(f"{log_prefix}: incomplete response", extra={"user_id": user_id, "status": response.status})
        return PromptOutcome(parsed=None, reason="incomplete_response")
    raw = response.output_text
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict):
        logger.warning(f"{log_prefix}: unparseable LLM response", extra={"user_id": user_id, "raw": (raw or "")[:200]})
        return PromptOutcome(parsed=None, reason="unparseable")
    return PromptOutcome(parsed=parsed, reason=None)
