from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from components import (
    DEFAULT_LANGUAGE,
    SESSION_TO_GLOBAL_KEY_ALIASES,
    SETTINGS,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    format_day_marker,
    format_local_date_str,
    format_time_anchor,
    resolve_language,
    safe_json_loads,
    utc_now,
)
from modules.auth import ChatRequestClientContext
from modules.companion import Persona
from modules.conversation import Conversation, Message
from modules.system import ChatRequest
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope, MemorySource
from services.domains.companion import build_system_prompt_extras, get_disturbance_tier, load_character_snapshot
from services.domains.configuration import DEFAULT_CONFIG
from services.domains.conversation import (
    DEFAULT_PRESET_ID,
    IM_KIND,
    SPECIAL_KIND,
    InferenceDefaults,
    companion_context_content,
    load_context_messages,
    resolve_preset_meta,
)
from services.domains.memory import (
    build_user_profile_extras,
    format_background_memory_block,
    format_proactive_memory_block,
    resolve_user_timezone,
    retrieve_proactive_memories,
)
from services.infrastructure.llm import (
    PRODUCT_REASONING_EFFORTS,
    ChatProvider,
    MissingLlmConfigError,
    ProviderConfig,
    ReasoningEffort,
    approx_responses_tokens,
    build_provider,
    message_to_response_items,
    resolve_context_tokens,
    resolve_provider_chain,
    resolve_reply_voice,
    resolve_video_chain,
    resolve_vision_chain,
)
from services.infrastructure.tool_runtime import REGISTRY, apply_search_tools_catalog, schema_name

from .native_memory import NativeMemory
from .prompt_blocks import AgentPromptConfig
from .prompt_presets import preset_excluded_tool_names
from .system_prompt import build_system_prompt


@dataclass(frozen=True)
class TurnInputs:
    """``build_turn_inputs`` 的输出：回合编排与各轮辅助函数所需字段，避免重复查询 DB。"""

    context: dict[str, Any]
    client: AsyncOpenAI
    native_memory: NativeMemory | None
    model_name: str
    ctx_length: int
    all_schemas: list[dict]
    # 会话预设与调用方排除的工具；装配与执行层共用。
    excluded_tool_names: frozenset[str]
    first_user_msg_content: str | None
    llm_chain: list[ProviderConfig]
    provider_name: str
    estimated_tokens: int
    user_local_tz: str | None
    language: str
    speech_config: ProviderConfig | None
    response_preference: Literal["text", "voice"]
    speech_voice: str


async def load_memory_query_text(db: AsyncSession, conv: Conversation, req: ChatRequest, *, use_request: bool) -> str:
    """召回查询取本轮用户输入；主动回合的请求是内部资料，改取历史中最近的用户发言。"""
    if use_request:
        return req.message.content or ""
    history = await load_context_messages(db, conv)
    return next((m.content or "" for m in reversed(history) if m.role == "user"), "")


def resolve_inference_settings(settings: dict[str, Any], *, conv: Conversation) -> InferenceDefaults:
    defaults = (
        resolve_preset_meta(conv.system_preset_id).inference_defaults
        if conv.kind in {SPECIAL_KIND, IM_KIND}
        else InferenceDefaults(
            DEFAULT_CONFIG["agent"]["temperature"],
            SETTINGS.context_compression_threshold,
            DEFAULT_CONFIG["agent"]["reasoning_effort"],
        )
    )
    reasoning = settings.get("agent.reasoning_effort")
    reasoning = _parse_reasoning_effort(reasoning) if isinstance(reasoning, str) else None
    threshold = parse_temperature(
        settings.get("chat.context_compression_threshold"),
        defaults.context_compression_threshold,
    )
    return InferenceDefaults(
        temperature=parse_temperature(settings.get("agent.temperature"), defaults.temperature),
        context_compression_threshold=threshold if threshold >= 0.3 else defaults.context_compression_threshold,
        reasoning_effort=cast(ReasoningEffort, reasoning or defaults.reasoning_effort),
    )


def merge_session_settings(
    user_settings: dict[str, Any],
    session_settings: dict[str, Any] | None,
    *,
    conv: Conversation,
) -> dict[str, Any]:
    """特殊会话使用场景默认值，普通会话继承工作台设置；最后合并会话覆盖。推理参数由 ``resolve_inference_settings`` 从结果解析。"""
    isolated = conv.kind in {SPECIAL_KIND, IM_KIND}
    merged = {
        key: value for key, value in user_settings.items() if not isolated or not key.startswith(("agent.", "chat."))
    }
    if isolated:
        merged.update(
            {
                f"chat.{key}": value
                for key, value in DEFAULT_CONFIG["chat"].items()
                if key != "context_compression_threshold"
            },
        )
    if session_settings:
        for k, v in session_settings.items():
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


def db_message_to_response_items(msg: Message) -> list[dict[str, Any]]:
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


def _user_row_has_video_part(msg: Message) -> bool:
    """多模态用户行是否含 ``input_video`` part；链选择据此优先走视频能力供应商。"""
    if msg.role != "user" or msg.content_type != "multimodal_v1":
        return False
    parsed = safe_json_loads(msg.content or "", default=[])
    return isinstance(parsed, list) and any(isinstance(p, dict) and p.get("type") == "input_video" for p in parsed)


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
    db_msgs: list[Message],
    system_prompt: str,
    *,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
    inject_time_perception: bool = True,
) -> dict[str, Any]:
    """DB 消息转 Responses 上下文；所有会话保留原始 call/result 工具帧。陪伴预设插入日期分界与用户时刻为独立输入项，工作预设跳过。"""
    context: dict[str, Any] = {"instructions": system_prompt, "input": [], "source_message_ids": []}
    prev_date_key: str | None = None
    last_user_at: datetime | None = None

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
        context["input"].extend(db_message_to_response_items(msg))
        if inject_time_perception and msg.role == "user" and msg.created_at is not None:
            clock = format_time_anchor(msg.created_at, last_user_at, user_local_tz, lang)
            if clock:
                context["input"].append(user_text_item(clock))
            last_user_at = msg.created_at
        source_id = msg.summary_through_message_id if msg.subtype in ("daily_summary", "compress_summary") else msg.id
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
        context["source_message_ids"].extend([None] * (len(context["input"]) - item_start))

    return context


def _find_authoritative_token_baseline(history: list[Message]) -> tuple[int | None, list[Message]]:
    """寻找最新已记录的 Token 基线；所有会话均标准保留工具帧，基线统一可靠。"""
    for i in range(len(history) - 2, -1, -1):
        msg = history[i]
        if msg.role == "assistant" and msg.prompt_tokens > 0:
            return msg.prompt_tokens + msg.completion_tokens, history[i + 1 :]
    return None, history


async def build_turn_inputs(
    db: AsyncSession,
    conv: Conversation,
    user_id: int,
    req: ChatRequest,
    session_client_context: ChatRequestClientContext | None,
    user_settings: dict[str, Any],
    memory_scope: MemoryScope | None,
    *,
    proactive_memory_query: str = "",
    proactive_memory_embedding: list[float] | None = None,
    companion_proactive_turn: bool = False,
    excluded_tool_names: frozenset[str] = frozenset(),
) -> TurnInputs:
    """解析身份 prompt、schemas、历史与 LLM client。``memory_scope`` 由调用方经 ``conversation_memory_scope`` 校验；自动化会话没有记忆域。"""
    preset_id = conv.system_preset_id
    is_companion = preset_id == DEFAULT_PRESET_ID
    history = await load_context_messages(db, conv)
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    # 历史含媒体时筛选到对应能力供应商（视频优先于图片），确保压缩与流式共用同一 _chain；链为空显式报错不回落文本链，否则换来网关拒收 input_video 的 400。
    llm_chain: list[ProviderConfig] = []
    if any(_user_row_has_video_part(m) for m in history):
        llm_chain = await resolve_video_chain(db, user_id)
        if not llm_chain:
            raise MissingLlmConfigError("当前供应商链中没有支持视频理解的模型，无法继续包含视频附件的对话")
    elif any(m.content_type == "multimodal_v1" for m in history if m.role == "user"):
        llm_chain = await resolve_vision_chain(db, user_id)
    if not llm_chain:
        llm_chain = await resolve_provider_chain(db, user_id, "llm")
        if not llm_chain:
            raise MissingLlmConfigError("no provider configured for service 'llm'")
    provider = build_provider(llm_chain[0], ChatProvider)

    excluded_tool_names = excluded_tool_names | preset_excluded_tool_names(preset_id)
    # 排除后重算 search_tools 的业务域清单，只列出本回合实际可解锁的能力。
    all_schemas = apply_search_tools_catalog(
        [
            schema
            for schema in REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
            if schema_name(schema) not in excluded_tool_names
        ],
    )
    persona = (
        (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if is_companion
        else None
    )
    # 入口处一次性 normalize 语言：避免 lang="fr" 等未支持值在 volatile header 与 day marker 处分别走不同分支。
    session_lang = resolve_language(user_settings.get("language"))
    # 自动化任务没有记忆域，不装配用户画像或长期记忆；其它 preset 即使 persona 未完成也能承载背景上下文。
    user_profile_extras = ""
    background_memory_extras = ""
    proactive_rows: list[dict] = []
    if memory_scope is not None:
        user_profile_extras = await build_user_profile_extras(db, memory_scope, language=session_lang)
        background_memory_extras = await format_background_memory_block(db, memory_scope, language=session_lang)
        if proactive_memory_query:
            proactive_rows = await retrieve_proactive_memories(
                db,
                memory_scope,
                proactive_memory_query,
                query_embedding=proactive_memory_embedding,
                limit=3,
            )
    user_local_tz = await resolve_user_timezone(db, user_id)
    agent_config = AgentPromptConfig(
        language=session_lang,
        valid_tool_names=[schema_name(s) for s in all_schemas],
        companion_proactive_turn=companion_proactive_turn,
        client_context=_merge_client_context(session_client_context, req.client_context),
        persona_extras=build_system_prompt_extras(
            persona,
            language=session_lang,
            character=await load_character_snapshot(db, user_id) if persona is not None else None,
        ),
        user_profile_extras=user_profile_extras,
        background_memory_extras=background_memory_extras,
        proactive_memory_extras=format_proactive_memory_block(proactive_rows, language=session_lang),
        user_local_tz=user_local_tz,
    )
    # 仅陪伴预设（生活空间 / special Cron）插入时间提示与跨日分界；工作台预设只保留 volatile header 的日期。
    context = _history_to_responses_context(
        history,
        build_system_prompt(agent_config, preset_id=preset_id),
        user_local_tz=user_local_tz,
        lang=session_lang,
        inject_time_perception=is_companion,
    )

    # 不绑定 session：每次 memory 工具调用各自开 session，连接不跨 LLM 循环持续占用。
    native_memory = (
        NativeMemory(memory_scope, source=MemorySource("tool", session_id=conv.id)) if memory_scope else None
    )

    # Token 估算结合 Responses 权威基线与 CJK 全量/增量估算。
    full_context_tokens = approx_responses_tokens(context["instructions"], context["input"])
    baseline, subsequent_msgs = _find_authoritative_token_baseline(history)
    if baseline is not None:
        delta_items = [item for m in subsequent_msgs for item in db_message_to_response_items(m)]
        baseline_tokens = baseline + approx_responses_tokens("", delta_items)
        # 提示词与 Schema 漂移保护：若基线估算与当前全量装配的上下文差异过大（>20% 且 >200 tokens），采用全量估算
        drift = abs(baseline_tokens - full_context_tokens)
        estimated_tokens = full_context_tokens if drift > max(200, int(full_context_tokens * 0.2)) else baseline_tokens
    else:
        estimated_tokens = full_context_tokens

    # 语音只用于陪伴固定会话；主动回合仅在自主档位提供语音能力。
    speech_config: ProviderConfig | None = None
    speech_voice = ""
    if (
        conv.kind == SPECIAL_KIND
        and is_companion
        and (not companion_proactive_turn or await get_disturbance_tier(user_id, db=db) == "autonomous")
    ):
        selected_voice = user_settings.get("companion.voice_id")
        speech_config, speech_voice = await resolve_reply_voice(
            db,
            user_id,
            selected_voice if isinstance(selected_voice, str) else "",
            session_lang,
        )

    return TurnInputs(
        context=context,
        client=provider.raw_client(),
        native_memory=native_memory,
        model_name=provider.config.model,
        ctx_length=resolve_context_tokens(provider.provider_name),
        all_schemas=all_schemas,
        excluded_tool_names=excluded_tool_names,
        first_user_msg_content=first_user_msg_content,
        llm_chain=llm_chain,
        provider_name=provider.provider_name,
        estimated_tokens=estimated_tokens,
        user_local_tz=user_local_tz,
        language=session_lang,
        speech_config=speech_config,
        speech_voice=speech_voice,
        response_preference=req.response_preference
        or ("voice" if user_settings.get("companion.response_preference") == "voice" else "text"),
    )


def _parse_reasoning_effort(raw: str | None) -> str | None:
    """规范化持久化的 reasoning_effort：``None`` 或集合外的值表示「不传参」，API 拒绝未知值，仅透传枚举成员。"""
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in PRODUCT_REASONING_EFFORTS else None


def parse_temperature(raw: Any, default: float) -> float:
    """解析并校验归一化温度；空、非数值或越界 [0, 1] 回退到 default。"""
    if raw is None:
        return default
    try:
        val = float(raw)
        return val if TEMPERATURE_MIN <= val <= TEMPERATURE_MAX else default
    except (ValueError, TypeError):
        return default
