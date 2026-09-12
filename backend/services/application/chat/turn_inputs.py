import json
from dataclasses import asdict, dataclass
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
    get_logger,
    resolve_language,
    safe_json_loads,
    utc_now,
)
from modules.auth import ChatRequestClientContext
from modules.companion import Persona
from modules.conversation import Conversation, Message
from modules.settings import UserSetting
from modules.system import AgentPromptConfig, ChatRequest, PromptPreset
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.application.chat.native_memory import NativeMemory
from services.domains.companion import build_outfit_extras, build_system_prompt_extras, is_work_preset
from services.domains.configuration.desktop_config import DEFAULT_CONFIG
from services.domains.conversation import (
    IM_KIND,
    SPECIAL_KIND,
    UI_ONLY_SUBTYPES,
    InferenceDefaults,
    resolve_preset_meta,
)
from services.domains.memory import (
    build_user_profile_extras,
    format_auto_inject_block,
    format_inferred_profile_block,
    format_proactive_memory_block,
    resolve_user_timezone,
    retrieve_proactive_memories,
)
from services.infrastructure.llm import (
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    approx_responses_tokens,
    message_to_response_items,
    provider_from_config,
    resolve_context_tokens,
    resolve_provider_chain,
    resolve_speech_style_config,
    resolve_video_chain,
    resolve_vision_chain,
)
from services.infrastructure.tool_runtime import REGISTRY, schema_name

from .prompt_presets import (
    AUTOMATION_EXCLUDED_TOOL_NAMES,
    AUTOMATION_PRESET,
    DEFAULT_PRESET_ID,
    LIFE_SPACE_TOOL_NAMES,
    resolve_preset,
)
from .system_prompt import build_system_prompt

logger = get_logger(__name__)


# 三家 Responses 供应商共同接受的安全枚举；供应商专属档位在 provider 层过滤。
ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high"})


@dataclass(frozen=True)
class TurnInputs:
    """``build_turn_inputs`` 的输出：orchestrator 与各轮辅助函数所需字段，避免重复查询 DB。"""

    context: dict[str, Any]
    client: Any
    native_memory: NativeMemory
    model_name: str
    model_override: str | None
    ctx_length: int
    context_tokens_override: int | None
    all_schemas: list[dict]
    first_user_msg_content: str | None
    llm_chain: list[ProviderConfig] | None
    provider_name: str = ""
    estimated_tokens: int = 0
    user_local_tz: str | None = None
    language: str = DEFAULT_LANGUAGE
    speech_config: ProviderConfig | None = None


async def load_user_settings(db: AsyncSession, user_id: int) -> dict[str, str]:
    rows = (await db.execute(select(UserSetting).where(UserSetting.user_id == user_id))).scalars().all()
    return {s.setting_key: s.setting_value for s in rows}


async def _load_memory_query_text(
    db: AsyncSession,
    conv: Conversation,
    req: ChatRequest,
    *,
    use_request: bool,
) -> str:
    if use_request and req.message.role == "user":
        return req.message.content or ""
    checkpoint_id = (
        await db.execute(
            select(func.max(Message.id)).where(
                Message.conversation_id == conv.id,
                Message.subtype.in_(("daily_summary", "compress_summary")),
            ),
        )
    ).scalar()
    stmt = select(Message.content).where(Message.conversation_id == conv.id, Message.role == "user")
    if checkpoint_id:
        stmt = stmt.where(Message.id >= checkpoint_id)
    content = (
        await db.execute(stmt.order_by(Message.id.asc() if use_request else Message.id.desc()).limit(1))
    ).scalar()
    return content or ""


def _resolve_turn_preset(conv: Conversation, preset_override: PromptPreset | None) -> PromptPreset:
    return preset_override or (AUTOMATION_PRESET if conv.is_automation else resolve_preset(conv.system_preset_id))


def resolve_inference_settings(settings: dict[str, str], *, conv: Conversation) -> InferenceDefaults:
    defaults = (
        resolve_preset_meta(conv.system_preset_id).inference_defaults
        if conv.kind in {SPECIAL_KIND, IM_KIND}
        else InferenceDefaults(
            DEFAULT_CONFIG["agent"]["temperature"],
            SETTINGS.context_compression_threshold,
            DEFAULT_CONFIG["agent"]["reasoning_effort"],
        )
    )
    raw_reasoning = settings.get("agent.reasoning_effort") or settings.get("reasoning_effort", "")
    reasoning = safe_json_loads(raw_reasoning, default=raw_reasoning)
    reasoning = _parse_reasoning_effort(reasoning) if isinstance(reasoning, str) else None
    threshold = parse_temperature(
        settings.get("chat.context_compression_threshold"),
        defaults.context_compression_threshold,
    )
    return InferenceDefaults(
        temperature=parse_temperature(settings.get("agent.temperature"), defaults.temperature),
        context_compression_threshold=threshold if threshold >= 0.3 else defaults.context_compression_threshold,
        reasoning_effort=cast(Literal["none", "low", "medium", "high"], reasoning or defaults.reasoning_effort),
    )


def merge_session_settings(
    user_settings: dict[str, str],
    session_settings: dict[str, Any] | None,
    *,
    conv: Conversation,
) -> dict[str, str]:
    """特殊会话使用场景默认值，普通会话继承工作台设置；最后合并会话覆盖。"""
    isolated = conv.kind in {SPECIAL_KIND, IM_KIND}
    merged = {
        key: value
        for key, value in user_settings.items()
        if not isolated
        or (not key.startswith(("agent.", "chat.")) and key not in {"reasoning_effort", "enable_background_review"})
    }
    if isolated:
        merged.update(
            {
                f"chat.{key}": value if isinstance(value, str) else json.dumps(value)
                for key, value in DEFAULT_CONFIG["chat"].items()
                if key != "context_compression_threshold"
            },
        )
    if session_settings:
        for k, v in session_settings.items():
            target_key = SESSION_TO_GLOBAL_KEY_ALIASES.get(k, k)
            merged[target_key] = v if isinstance(v, str) else json.dumps(v)
    inference = resolve_inference_settings(merged, conv=conv)
    merged.update(
        {
            SESSION_TO_GLOBAL_KEY_ALIASES[key]: value if isinstance(value, str) else json.dumps(value)
            for key, value in asdict(inference).items()
        },
    )
    return merged


def _merge_client_context(
    session_ctx: ChatRequestClientContext | None,
    request_ctx: ChatRequestClientContext | None,
) -> ChatRequestClientContext | None:
    """request 覆盖 session；任一可为 None。"""
    if not session_ctx and not request_ctx:
        return None
    merged = (session_ctx.model_dump(exclude_none=True) if session_ctx else {}) | (
        request_ctx.model_dump(exclude_none=True) if request_ctx else {}
    )
    return ChatRequestClientContext.model_validate(merged) if merged else None


def db_message_to_response_items(msg: Message) -> list[dict[str, Any]]:
    """DB Message -> Responses API input items. 正文保持入库原文，不拼接时间标记。"""
    if msg.subtype in UI_ONLY_SUBTYPES:
        return []

    content_val: str | list = msg.content or ""
    is_multimodal = getattr(msg, "content_type", "text") == "multimodal_v1"
    if is_multimodal:
        parsed = safe_json_loads(content_val if isinstance(content_val, str) else "")
        content_val = parsed if isinstance(parsed, list) else content_val

    if msg.role == "system":
        return [{"role": "user", "content": [{"type": "input_text", "text": content_val or ""}]}]

    if msg.role == "assistant":
        has_tool_calls = bool((msg.tool_calls or "").strip())
        if not has_tool_calls and not is_multimodal and not (content_val or "").strip():
            return []

    item: dict = {"role": msg.role, "content": content_val}
    if msg.tool_call_id:
        item["tool_call_id"] = msg.tool_call_id
    items: list[dict[str, Any]] = message_to_response_items(item)
    if msg.role == "assistant" and msg.tool_calls and (calls := safe_json_loads(msg.tool_calls)) is not None:
        for call in calls:
            if isinstance(call, dict):
                items.append(call)
    return items


def _user_row_has_video_part(msg: Message) -> bool:
    """多模态用户行是否含 ``input_video`` part；链选择据此优先走视频能力供应商。"""
    if msg.role != "user" or getattr(msg, "content_type", "text") != "multimodal_v1":
        return False
    parsed = safe_json_loads(msg.content if isinstance(msg.content, str) else "", default=[])
    return isinstance(parsed, list) and any(isinstance(p, dict) and p.get("type") == "input_video" for p in parsed)


def _user_time_item(text: str) -> dict[str, Any]:
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
            items.append(_user_time_item(marker_text))
    return cur_date_key or prev_date_key


def _history_to_responses_context(
    db_msgs: list[Message],
    system_prompt: str,
    *,
    user_local_tz: str | None = None,
    lang: str = DEFAULT_LANGUAGE,
    inject_time_perception: bool = True,
) -> dict[str, Any]:
    """DB 消息列表转为 Responses API 上下文。完整保留所有会话中的原始 call/result 工具帧。

    陪伴预设把日期分界与用户时刻作为独立输入项插入，不写入消息正文；工作预设跳过。
    """
    context: dict[str, Any] = {"instructions": system_prompt, "input": []}
    prev_date_key: str | None = None
    last_user_at: datetime | None = None

    valid_msgs = [m for m in db_msgs if getattr(m, "subtype", None) not in UI_ONLY_SUBTYPES]

    for msg in valid_msgs:
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
                context["input"].append(_user_time_item(clock))
            last_user_at = msg.created_at

    if inject_time_perception and (not valid_msgs or valid_msgs[-1].role != "user"):
        now = utc_now()
        prev_date_key = _maybe_append_day_marker(context["input"], now, prev_date_key, user_local_tz, lang)
        clock = format_time_anchor(now, None, user_local_tz, lang)
        if clock:
            context["input"].append(_user_time_item(clock))

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
    user_settings: dict,
    preset_override: PromptPreset | None = None,
    use_request_for_memory_retrieval: bool = True,
    proactive_memory_query: str | None = None,
    proactive_memory_embedding: list[float] | None = None,
) -> TurnInputs:
    """解析身份 prompt、schemas、agent_config、历史与 LLM client；native_memory 补充内容在此注入系统消息，使 orchestrator 保持线性。"""
    # LLM 上下文从最新检查点开始（夜间 daily_summary 或进行中 compress_summary），其前消息已被摘要覆盖；原行留在 DB，仅缩窄本次读取范围。
    checkpoint_id = (
        await db.execute(
            select(func.max(Message.id)).where(
                Message.conversation_id == conv.id,
                Message.subtype.in_(("daily_summary", "compress_summary")),
            ),
        )
    ).scalar()

    stmt = select(Message).where(Message.conversation_id == conv.id)
    if checkpoint_id:
        stmt = stmt.where(Message.id >= checkpoint_id)
    history = (await db.execute(stmt.order_by(Message.id.asc()))).scalars().all()
    first_user_msg = next((m for m in history if m.role == "user"), None)
    first_user_msg_content = first_user_msg.content if first_user_msg else None

    # 历史含媒体时把 LLM 链筛选到对应能力供应商，确保压缩客户端与流式调用（接收同一 _chain）都能消费媒体 part。
    # 视频判定优先于图片（视频链通常也具备视觉，反之不然）；链为空时显式报错而非回落文本链——
    # 回落只会换来供应商网关拒收 input_video 的不可读 400。
    turn_has_video = any(_user_row_has_video_part(m) for m in history)
    turn_has_images = any(getattr(m, "content_type", "text") == "multimodal_v1" for m in history if m.role == "user")
    llm_chain = None
    provider = None
    if turn_has_video:
        video_chain = await resolve_video_chain(db, user_id)
        if not video_chain:
            raise MissingLlmConfigError("当前供应商链中没有支持视频理解的模型，无法继续包含视频附件的对话")
        llm_chain = video_chain
        provider = provider_from_config(video_chain[0])
    elif turn_has_images:
        vision_chain = await resolve_vision_chain(db, user_id)
        if vision_chain:
            llm_chain = vision_chain
            provider = provider_from_config(vision_chain[0])
    if provider is None:
        llm_chain = await resolve_provider_chain(db, user_id, "llm")
        if not llm_chain:
            raise MissingLlmConfigError("no provider configured for service 'llm'")
        provider = provider_from_config(llm_chain[0])
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"llm provider '{provider.provider_name}' does not expose the Responses API")
    model_name = req.model or provider.config.model
    if req.context_tokens is not None:
        ctx_length = req.context_tokens
    else:
        if req.model and req.model != provider.config.model:
            # 渲染端覆写模型但未钉住窗口：告警以便预算不匹配的问题能在日志中暴露。
            logger.warning(
                "request model override without context_tokens",
                extra={"provider": provider.provider_name, "request_model": req.model},
            )
        ctx_length = resolve_context_tokens(provider.provider_name, ServiceType.llm)

    resolved_preset = _resolve_turn_preset(conv, preset_override)
    include_companion_context = resolved_preset.id != "automation"
    identity_prompt = None
    if include_companion_context:
        identity_prompt = (
            await db.execute(
                select(UserSetting.setting_value).where(
                    UserSetting.user_id == user_id,
                    UserSetting.setting_key == "identity_prompt",
                ),
            )
        ).scalar()
    all_schemas = REGISTRY.get_all_schemas(user_id, user_settings=user_settings)
    if not include_companion_context:
        all_schemas = [schema for schema in all_schemas if schema_name(schema) not in AUTOMATION_EXCLUDED_TOOL_NAMES]
    elif is_work_preset(conv.system_preset_id):
        # 用 DB 原始 preset 判定（含 pm 别名）：工作会话不绑定生活空间工具，而非靠工具入口拒绝。
        all_schemas = [schema for schema in all_schemas if schema_name(schema) not in LIFE_SPACE_TOOL_NAMES]
    persona = (
        (await db.execute(select(Persona).where(Persona.user_id == user_id))).scalar_one_or_none()
        if include_companion_context
        else None
    )
    # 入口处一次性 normalize 语言：避免 lang="fr" 等未支持值在 volatile header 与 day marker 处分别走不同分支；
    # 上移至此是为了让下方 auto_inject_extras / inferred_profile_extras / proactive_memory_extras /
    # user_profile_extras / outfit_extras 都能拿到正确的 language，而非默认值 zh。
    session_lang = resolve_language(user_settings.get("language", DEFAULT_LANGUAGE))
    user_profile_extras = (
        await build_user_profile_extras(db, user_id, language=session_lang)
        if persona is not None and persona.is_complete
        else ""
    )
    outfit_extras = (
        await build_outfit_extras(db, user_id, language=session_lang)
        if persona is not None and persona.is_complete
        else ""
    )
    # 自动化任务不装配伙伴人格、用户画像或长期记忆；其它 preset 即使 persona 未完成也能承载背景上下文。
    auto_inject_extras = (
        await format_auto_inject_block(db, user_id, language=session_lang) if include_companion_context else ""
    )
    inferred_profile_extras = (
        await format_inferred_profile_block(db, user_id, language=session_lang) if include_companion_context else ""
    )
    user_local_tz = await resolve_user_timezone(db, user_id)
    last_history_user_content = next(
        (m.content for m in reversed(history) if m.role == "user" and m.content),
        first_user_msg_content,
    )
    query_text = proactive_memory_query
    if query_text is None:
        query_text = (
            (req.message.content if req.message.role == "user" else first_user_msg_content)
            if use_request_for_memory_retrieval
            else last_history_user_content
        ) or ""
    proactive_rows = (
        await retrieve_proactive_memories(db, user_id, query_text, query_embedding=proactive_memory_embedding, limit=3)
        if include_companion_context and query_text
        else []
    )
    proactive_memory_extras = format_proactive_memory_block(proactive_rows, language=session_lang)
    # 仅陪伴预设（生活空间 / special Cron）插入时间提示与跨日分界；
    # 工作台预设只保留 volatile header 的日期。
    inject_time_perception = resolved_preset.id == DEFAULT_PRESET_ID
    agent_config = AgentPromptConfig(
        valid_tool_names=[schema_name(s) for s in all_schemas],
        model=model_name,
        tools=all_schemas,
        client_context=_merge_client_context(session_client_context, req.client_context),
        identity_prompt=identity_prompt,
        persona_extras=build_system_prompt_extras(persona, language=session_lang),
        user_profile_extras=user_profile_extras,
        outfit_extras=outfit_extras,
        auto_inject_extras=auto_inject_extras,
        inferred_profile_extras=inferred_profile_extras,
        proactive_memory_extras=proactive_memory_extras,
        language=session_lang,
        user_local_tz=user_local_tz,
    )
    context = _history_to_responses_context(
        history,
        build_system_prompt(agent_config, preset=resolved_preset),
        user_local_tz=user_local_tz,
        lang=session_lang,
        inject_time_perception=inject_time_perception,
    )

    # 不绑定 session：每次 memory 工具调用各自开 session，连接不跨 LLM 循环持续占用。
    native_memory = NativeMemory(user_id)
    if include_companion_context and (addition := native_memory.format_for_system_prompt()):
        context["instructions"] += "\n\n" + addition

    # 计算上下文 Token 估算值：结合 Responses 权威基线与 CJK 全量/增量估算
    full_context_tokens = approx_responses_tokens(context["instructions"], context["input"])
    baseline, subsequent_msgs = _find_authoritative_token_baseline(history)
    if baseline is not None:
        delta_items = [item for m in subsequent_msgs for item in db_message_to_response_items(m)]
        delta_tokens = approx_responses_tokens("", delta_items)
        baseline_tokens = baseline + delta_tokens
        # 提示词与 Schema 漂移保护：若基线估算与当前全量装配的上下文差异过大（>20% 且 >200 tokens），采用全量估算
        drift = abs(baseline_tokens - full_context_tokens)
        estimated_tokens = full_context_tokens if drift > max(200, int(full_context_tokens * 0.2)) else baseline_tokens
    else:
        estimated_tokens = full_context_tokens

    speech_config = None
    if conv.kind == SPECIAL_KIND and resolved_preset.id == DEFAULT_PRESET_ID:
        raw_voice = user_settings.get("companion.voice_id", "")
        selected_voice = safe_json_loads(raw_voice, default=raw_voice)
        speech_config = await resolve_speech_style_config(
            db,
            user_id,
            selected_voice if isinstance(selected_voice, str) else "",
            session_lang,
        )

    return TurnInputs(
        context=context,
        client=client,
        native_memory=native_memory,
        model_name=model_name,
        model_override=req.model,
        ctx_length=ctx_length,
        context_tokens_override=req.context_tokens,
        all_schemas=all_schemas,
        first_user_msg_content=first_user_msg_content,
        llm_chain=llm_chain,
        provider_name=provider.provider_name,
        estimated_tokens=estimated_tokens,
        user_local_tz=user_local_tz,
        language=session_lang,
        speech_config=speech_config,
    )


def _parse_reasoning_effort(raw: str | None) -> str | None:
    """规范化持久化的 reasoning_effort：``None`` 或集合外的值表示「不传参」，API 拒绝未知值，仅透传枚举成员。"""
    if not raw:
        return None
    raw = raw.strip().lower()
    return raw if raw in ALLOWED_REASONING_EFFORTS else None


def parse_temperature(raw: Any, default: float) -> float:
    """解析并校验归一化温度；空、非数值或越界 [0, 1] 回退到 default。"""
    if raw is None:
        return default
    try:
        val = float(raw)
        return val if TEMPERATURE_MIN <= val <= TEMPERATURE_MAX else default
    except (ValueError, TypeError):
        return default
