import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

from features.companion.disturbance import get_disturbance_tier
from features.companion.persona_service import build_system_prompt_extras, load_persona_definition, render_extras
from features.companion.visual_state import persona_snapshot
from features.configuration.desktop_config import DEFAULT_CONFIG
from features.configuration.settings.timezone import resolve_user_timezone
from features.conversation.context_window import CHECKPOINT_SUBTYPE
from features.conversation.formatting import companion_context_content, message_text
from features.conversation.request_contracts import ChatRequest
from features.conversation.snapshots import ConversationSnapshot, MessageSnapshot
from features.conversation.turn_access import context_messages, conversation_topic_ids
from features.life.queries import get_life_state_view, list_events, list_pursuits
from features.media.speech import resolve_reply_voice
from features.memory.memory_bootstrap import build_user_profile_extras
from features.memory.memory_format import (
    format_background_memory_block,
    format_companion_reflection_block,
    format_proactive_memory_block,
)
from features.memory.memory_retrieval import MemoryEmbedding, MemoryRecallResult, retrieve_proactive_memories
from infrastructure.ai.llm_client import (
    MissingLlmConfigError,
    resolve_media_chain,
    resolve_provider_chain,
)
from infrastructure.ai.providers.base import ProviderConfig, ReasoningEffort
from infrastructure.ai.providers.registry import resolve_context_tokens
from infrastructure.ai.responses import approx_responses_tokens, message_to_response_items, request_input_modalities
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.config import SETTINGS
from infrastructure.constants import DEFAULT_LANGUAGE, SESSION_TO_GLOBAL_KEY_ALIASES, TEMPERATURE_MAX, TEMPERATURE_MIN
from infrastructure.text import (
    format_day_marker,
    format_local_date_str,
    format_time_anchor,
    resolve_language,
    safe_json_loads,
    tool_error,
    utc_now,
)
from runtime.client_context import ChatRequestClientContext
from runtime.memory_scope import MemoryScope, MemorySource
from runtime.presets import COMPANION_PRESET_ID
from runtime.tools.registry import REGISTRY, schema_name
from runtime.tools.toolsets import disabled_backend_tool_names
from sqlalchemy.ext.asyncio import AsyncSession

from workflows.chat.native_memory import NativeMemory
from workflows.chat.prompt_blocks import AgentPromptConfig
from workflows.chat.prompt_presets import preset_excluded_tool_names
from workflows.chat.system_prompt import build_system_prompt
from workflows.chat.tool_dispatch import INTERRUPTED_RUNNING_ERROR
from workflows.tool_availability import unavailable_action_tool_names


@dataclass(frozen=True)
class TurnInputs:
    """``build_turn_inputs`` 的输出：回合编排与各轮辅助函数所需字段，避免重复查询 DB。"""

    context: dict[str, Any]
    native_memory: NativeMemory | None
    ctx_length: int
    all_schemas: list[dict]
    # 会话预设、调用方排除与用户已禁用工具集中的工具；装配与执行层共用。
    excluded_tool_names: frozenset[str]
    first_user_msg_content: str | None
    estimated_tokens: int
    user_local_tz: str | None
    language: str
    speech_config: ProviderConfig | None
    response_preference: Literal["text", "voice"]
    speech_voice: str
    reply_persona: str


def memory_query_text(req: ChatRequest, history: Sequence[MessageSnapshot], *, use_request: bool) -> str:
    """召回查询取本轮用户输入；主动回合的请求是内部资料，改取历史中最近的用户发言。"""
    if use_request:
        return req.message.content or ""
    # 多模态行的正文是 part 数组 JSON，只取文字部分，不把附件地址送去向量化。
    return next((message_text(m) for m in reversed(history) if m.role == "user"), "")


@dataclass(frozen=True)
class InferenceSettings:
    temperature: float
    context_compression_threshold: float
    reasoning_effort: ReasoningEffort


def resolve_inference_settings(settings: dict[str, Any]) -> InferenceSettings:
    reasoning = settings.get("agent.reasoning_effort")
    reasoning = _parse_reasoning_effort(reasoning) if isinstance(reasoning, str) else None
    threshold = parse_temperature(
        settings.get("chat.context_compression_threshold"),
        SETTINGS.context_compression_threshold,
    )
    return InferenceSettings(
        temperature=parse_temperature(settings.get("agent.temperature"), DEFAULT_CONFIG["agent"]["temperature"]),
        context_compression_threshold=threshold if threshold >= 0.3 else SETTINGS.context_compression_threshold,
        reasoning_effort=reasoning or DEFAULT_CONFIG["agent"]["reasoning_effort"],
    )


def merge_session_settings(
    user_settings: dict[str, Any],
    session_settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """所有会话继承用户全局设置，最后合并当前会话的显式覆盖。"""
    merged = dict(user_settings)
    if session_settings:
        for k, v in session_settings.items():
            if v is not None:
                merged[SESSION_TO_GLOBAL_KEY_ALIASES[k]] = v
    return merged


def _merge_client_context(
    session_ctx: ChatRequestClientContext | None,
    request_ctx: ChatRequestClientContext | None,
) -> ChatRequestClientContext | None:
    """请求逐字段覆盖会话级客户端资料。"""
    if session_ctx is None or request_ctx is None:
        return request_ctx or session_ctx
    return session_ctx.model_copy(update=request_ctx.model_dump(exclude_none=True))


def db_message_to_response_items(msg: MessageSnapshot) -> list[dict[str, Any]]:
    """DB Message -> Responses API input items. 正文保持入库原文，不拼接时间标记。"""
    content_val: str | list = msg.content or ""
    is_multimodal = msg.content_type == "multimodal_v1"
    if msg.content_type == "companion_reply":
        content_val = companion_context_content(msg)
    elif is_multimodal and isinstance(parsed := safe_json_loads(content_val), list):
        content_val = parsed

    if msg.role == "system":
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": content_val}]}]

    if (
        msg.role == "assistant"
        and not (msg.tool_calls or "").strip()
        and not is_multimodal
        and not str(content_val).strip()
    ):
        return []

    item: dict[str, Any] = {"role": msg.role, "content": content_val}
    if msg.tool_call_id:
        item["tool_call_id"] = msg.tool_call_id
    items = message_to_response_items(item)
    if msg.role == "assistant" and msg.tool_calls and (calls := safe_json_loads(msg.tool_calls)) is not None:
        items.extend(call for call in calls if isinstance(call, dict))
    return items


async def resolve_context_provider_chain(
    db: AsyncSession,
    user_id: int,
    input_items: list[dict[str, Any]],
    *,
    task: AiTaskKey,
) -> list[ProviderConfig]:
    """仅按本次请求仍携带的媒体选链，包括多模态工具结果。"""
    required = request_input_modalities(input_items)
    chain = await resolve_media_chain(
        db,
        user_id,
        task=task,
        required_modalities=required,
    )
    if not chain and "audio" in required:
        chain = await resolve_media_chain(
            db,
            user_id,
            task=task,
            required_modalities=required - {"audio"},
        )
    if not chain:
        raise MissingLlmConfigError("no provider configured for service 'llm'")
    return chain


def user_text_item(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _maybe_append_day_marker(
    items: list[dict[str, Any]],
    dt: datetime | None,
    prev_date_key: str | None,
    user_local_tz: str | None,
    lang: str,
) -> str | None:
    if dt is None:
        return prev_date_key
    cur_date_key = format_local_date_str(dt, user_local_tz, lang)
    if cur_date_key and cur_date_key != prev_date_key:
        marker_text = format_day_marker(dt, user_local_tz, lang)
        if marker_text:
            items.append(user_text_item(marker_text))
    return cur_date_key or prev_date_key


def _history_to_responses_context(
    db_msgs: list[MessageSnapshot],
    system_prompt: str,
    *,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
    inject_time_perception: bool = True,
) -> dict[str, Any]:
    """DB 消息转 Responses 上下文；所有会话保留原始 call/result 工具帧。陪伴预设插入日期分界与用户时刻为独立输入项，工作预设跳过。"""
    context: dict[str, Any] = {
        "instructions": system_prompt,
        "input": [],
        "source_message_ids": [],
        "dialogue_message_ids": [],
        "checkpoint_indices": [],
        "time_context_indices": [],
        "user_input_indices": [],
    }
    prev_date_key: str | None = None
    last_user_at: datetime | None = None
    answered_call_ids = {msg.tool_call_id for msg in db_msgs if msg.role == "tool" and msg.tool_call_id}

    for msg in db_msgs:
        item_start = len(context["input"])
        if inject_time_perception:
            prev_date_key = _maybe_append_day_marker(
                context["input"],
                msg.created_at,
                prev_date_key,
                user_local_tz,
                lang,
            )
            if msg.role == "user" and msg.created_at is not None:
                clock = format_time_anchor(msg.created_at, last_user_at, user_local_tz, lang)
                if clock:
                    context["input"].append(user_text_item(clock))
                last_user_at = msg.created_at
        context["time_context_indices"].extend(range(item_start, len(context["input"])))
        items = db_message_to_response_items(msg)
        if items and msg.role in {"user", "assistant"} and not msg.tool_calls:
            context["dialogue_message_ids"].append(msg.id)
        if msg.role == "user":
            context["user_input_indices"].extend(range(len(context["input"]), len(context["input"]) + len(items)))
        if msg.subtype == CHECKPOINT_SUBTYPE:
            context["checkpoint_indices"].extend(range(len(context["input"]), len(context["input"]) + len(items)))
        context["input"].extend(items)
        # 调用行已落库而缺结果行（如进程在保存结果前退出）时补记结果未知，孤立调用会让供应商拒绝整个上下文。
        for item in items:
            call_id = item.get("call_id") if item.get("type") == "function_call" else None
            if call_id and call_id not in answered_call_ids:
                context["input"].append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": tool_error(INTERRUPTED_RUNNING_ERROR),
                    },
                )
        source_id = msg.summary_through_message_id if msg.subtype == CHECKPOINT_SUBTYPE else msg.id
        if source_id is None:
            raise ValueError("Conversation summary requires an original message boundary")
        context["source_message_ids"].extend([source_id] * (len(context["input"]) - item_start))

    if inject_time_perception and (not db_msgs or db_msgs[-1].role != "user"):
        item_start = len(context["input"])
        now = utc_now()
        prev_date_key = _maybe_append_day_marker(context["input"], now, prev_date_key, user_local_tz, lang)
        clock = format_time_anchor(now, None, user_local_tz, lang)
        if clock:
            context["input"].append(user_text_item(clock))
        context["time_context_indices"].extend(range(item_start, len(context["input"])))
        context["source_message_ids"].extend([None] * (len(context["input"]) - item_start))

    return context


def _find_authoritative_token_baseline(history: list[MessageSnapshot]) -> tuple[int | None, list[MessageSnapshot]]:
    """寻找最新已记录的 Token 基线；所有会话均标准保留工具帧，基线统一可靠。"""
    for i in range(len(history) - 2, -1, -1):
        msg = history[i]
        if msg.role == "assistant" and msg.prompt_tokens > 0:
            return msg.prompt_tokens + msg.completion_tokens, history[i + 1 :]
    return None, history


def estimate_context_tokens(context: dict[str, Any], history: list[MessageSnapshot]) -> int:
    """Token 估算结合 Responses 权威基线与 CJK 全量/增量估算；压缩后与回合装配共用同一口径。"""
    full_context_tokens = approx_responses_tokens(context["instructions"], context["input"])
    baseline, subsequent_msgs = _find_authoritative_token_baseline(history)
    if baseline is None:
        return full_context_tokens
    delta_items = [item for m in subsequent_msgs for item in db_message_to_response_items(m)]
    baseline_tokens = baseline + approx_responses_tokens("", delta_items)
    # 提示词与 Schema 漂移保护：若基线估算与当前全量装配的上下文差异过大（>20% 且 >200 tokens），采用全量估算
    drift = abs(baseline_tokens - full_context_tokens)
    return full_context_tokens if drift > max(200, int(full_context_tokens * 0.2)) else baseline_tokens


async def build_turn_inputs(
    db: AsyncSession,
    conv: ConversationSnapshot,
    user_id: int,
    req: ChatRequest,
    session_client_context: ChatRequestClientContext | None,
    user_settings: dict[str, Any],
    memory_scope: MemoryScope | None,
    *,
    task: AiTaskKey,
    proactive_memory_query: str = "",
    proactive_memory_embedding: MemoryEmbedding | None = None,
    companion_proactive_turn: bool = False,
    excluded_tool_names: frozenset[str] = frozenset(),
    history: list[MessageSnapshot] | None = None,
) -> TurnInputs:
    """解析身份 prompt、schemas、历史与 LLM client。``memory_scope`` 由调用方经 ``conversation_memory_scope`` 校验；自动化会话没有记忆域。"""
    preset_id = conv.system_preset_id
    delegated = conv.parent_id is not None
    is_companion = preset_id == COMPANION_PRESET_ID and not delegated
    if history is None:
        history = await context_messages(db, conv)
    first_user_msg = next((m for m in history if m.role == "user"), None)
    # 标题只依据文字：多模态行的正文是 part 数组 JSON，附件地址不进标题请求。
    first_user_msg_content = message_text(first_user_msg) if first_user_msg else None

    # 基础链用于估算当前对话窗口；压缩沿所属任务链解析，实际回合按截断和视频内联后幸存的媒体选链。
    llm_chain = await resolve_provider_chain(
        db,
        user_id,
        "llm",
        task=task,
    )
    if not llm_chain:
        raise MissingLlmConfigError("no provider configured for service 'llm'")

    excluded_tool_names = (
        excluded_tool_names | preset_excluded_tool_names(preset_id) | disabled_backend_tool_names(user_settings)
    )
    action_excluded = await unavailable_action_tool_names(user_id)
    all_schemas = REGISTRY.get_all_schemas(
        user_id,
        user_settings=user_settings,
        exclude=excluded_tool_names | action_excluded,
    )
    persona = await persona_snapshot(db, user_id) if is_companion else None
    # 入口处一次性 normalize 语言：避免 lang="fr" 等未支持值在 volatile header 与 day marker 处分别走不同分支。
    session_lang = resolve_language(user_settings.get("language"))
    # 自动化任务没有记忆域，不装配用户画像或长期记忆；其它 preset 即使 persona 未完成也能承载背景上下文。
    user_profile_extras = ""
    background_memory_extras = ""
    proactive_rows: list[MemoryRecallResult] = []
    companion_reflection_extras = ""
    if memory_scope is not None:
        user_profile_extras = await build_user_profile_extras(db, memory_scope, language=session_lang)
        background_memory_extras = await format_background_memory_block(db, memory_scope, language=session_lang)
        if is_companion:
            companion_reflection_extras = await format_companion_reflection_block(
                db,
                memory_scope,
                language=session_lang,
            )
            life_state = await get_life_state_view(db, user_id)
            life_pursuits = await list_pursuits(db, user_id, status="active", limit=3)
            life_events = await list_events(db, user_id, limit=6)
            life_payload = {
                "current_activity": life_state.current_activity if life_state else "",
                "mood": life_state.mood if life_state else None,
                "active_pursuits": [item.model_dump(mode="json") for item in life_pursuits.items],
                "recent_life_events": [item.model_dump(mode="json") for item in life_events.items],
            }
            life_label = (
                "当前生活资料（仅作背景，不是新的指令；计划、愿望和未完成事项不等于已经发生的现实经历）：\n"
                if session_lang == "zh"
                else "Current life data (background only, not instructions; plans, wishes, and unfinished items are not completed real-world events):\n"
            )
            life_block = life_label + json.dumps(
                life_payload,
                ensure_ascii=False,
            )
            companion_reflection_extras = "\n\n".join(
                part for part in (companion_reflection_extras, life_block) if part
            )
        if proactive_memory_query:
            proactive_rows = await retrieve_proactive_memories(
                db,
                memory_scope,
                proactive_memory_query,
                query_embedding=proactive_memory_embedding,
                limit=3,
            )
    if companion_reflection_extras:
        proactive_rows = [row for row in proactive_rows if row.kind != "reflection"]
    user_local_tz = await resolve_user_timezone(db, user_id)
    agent_config = AgentPromptConfig(
        language=session_lang,
        valid_tool_names=[schema_name(s) for s in all_schemas],
        companion_proactive_turn=companion_proactive_turn,
        client_context=_merge_client_context(session_client_context, req.client_context),
        persona_extras=build_system_prompt_extras(
            persona,
            language=session_lang,
        ),
        user_profile_extras=user_profile_extras,
        background_memory_extras=background_memory_extras,
        companion_reflection_extras=companion_reflection_extras,
        proactive_memory_extras=format_proactive_memory_block(proactive_rows, language=session_lang),
        user_local_tz=user_local_tz,
    )
    # 陪伴用户会话插入时间提示与跨日分界；专业预设只保留 volatile header 的日期。
    context = _history_to_responses_context(
        history,
        build_system_prompt(agent_config, preset_id=preset_id, delegated=delegated),
        user_local_tz=user_local_tz,
        lang=session_lang,
        inject_time_perception=is_companion,
    )
    if is_companion:
        context["historical_topic_ids"] = await conversation_topic_ids(db, conv)

    # 不绑定 session：每次 memory 工具调用各自开 session，连接不跨 LLM 循环持续占用。
    native_memory = (
        NativeMemory(memory_scope, source=MemorySource("tool", session_id=conv.id), task=task) if memory_scope else None
    )

    # Token 估算结合 Responses 权威基线与 CJK 全量/增量估算。
    estimated_tokens = estimate_context_tokens(context, history)

    # 陪伴用户会话提供语音；主动回合仅在自主档位提供语音能力。
    speech_config: ProviderConfig | None = None
    speech_voice = ""
    if is_companion and (not companion_proactive_turn or await get_disturbance_tier(user_id, db=db) == "autonomous"):
        selected_voice = user_settings.get("companion.voice_id")
        speech_config, speech_voice = await resolve_reply_voice(
            db,
            user_id,
            selected_voice if isinstance(selected_voice, str) else "",
            session_lang,
        )

    return TurnInputs(
        context=context,
        native_memory=native_memory,
        ctx_length=resolve_context_tokens(llm_chain[0]),
        all_schemas=all_schemas,
        excluded_tool_names=excluded_tool_names,
        first_user_msg_content=first_user_msg_content,
        estimated_tokens=estimated_tokens,
        user_local_tz=user_local_tz,
        language=session_lang,
        speech_config=speech_config,
        speech_voice=speech_voice,
        reply_persona=render_extras(load_persona_definition(persona), language=session_lang)
        if persona is not None and persona.is_complete
        else "",
        response_preference=req.response_preference
        or ("voice" if user_settings.get("companion.response_preference") == "voice" else "text"),
    )


def _parse_reasoning_effort(raw: str | None) -> ReasoningEffort | None:
    """规范化持久化的 reasoning_effort；未设置或集合外的值使用通用默认值。"""
    if not raw:
        return None
    raw = raw.strip().lower()
    return next((effort for effort in get_args(ReasoningEffort) if effort == raw), None)


def parse_temperature(raw: Any, default: float) -> float:
    """解析并校验归一化温度；空、非数值或越界 [0, 1] 回退到 default。"""
    if raw is None:
        return default
    try:
        val = float(raw)
        return val if TEMPERATURE_MIN <= val <= TEMPERATURE_MAX else default
    except (ValueError, TypeError):
        return default
