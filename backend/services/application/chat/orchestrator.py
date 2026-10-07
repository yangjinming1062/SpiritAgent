import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from components import (
    CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
    safe_json_loads,
    session_scope,
    utc_now,
)
from modules.auth import ChatRequestClientContext
from modules.channels import ChannelTurnSource
from modules.conversation import Conversation, Message
from modules.settings import get_user_setting, load_user_settings, resolve_user_timezone
from modules.system import ChatMessageRequest, ChatRequest
from prompts.companion import PENDING_INTENTIONS_LABELS

from services.contracts import SceneTurnState
from services.domains.companion import list_companion_intents, user_turn_activity
from services.domains.conversation import (
    COMPANION_PRESET_ID,
    IM_KIND,
    SPECIAL_KIND,
    conversation_memory_scope,
    load_context_messages,
    load_media_turn,
    refresh_video_media,
)
from services.domains.media import inline_video_parts
from services.domains.memory import embed_memory_text
from services.infrastructure.assets import asset_write_context, dated_asset_directory
from services.infrastructure.llm import (
    ChatProvider,
    LLMRuntimeError,
    MissingLlmConfigError,
    UserLlmConfig,
    execute_with_fallback,
    generation_timeout_scope,
    resolve_context_tokens,
    scale_temperature,
)
from services.infrastructure.tool_runtime import (
    ToolCallGuardrailController,
    apply_search_tools_catalog,
    schema_name,
)
from services.infrastructure.turn_ownership import conversation_lock

from .chat_emitter import Emitter
from .context_compressor import CompressionFailedError, CompressionInfo, compress_history, compression_due
from .delegation import run_delegated_turn
from .message_sanitization import truncate_responses_context
from .persistence import (
    _persist_assistant_no_tool_turn,
    _persist_assistant_with_tool_calls_and_results,
    _persist_user_message,
    persist_compression_checkpoint,
)
from .prompt_presets import COMPANION_MEDIA_TOOL_NAMES
from .streaming import (
    _assign_tool_call_ids,
    _generate_llm_response,
    _IncompleteResponseError,
    _InvalidCompanionReplyError,
    _LLMTurnResult,
    _reply_repair_history,
)
from .system_prompt import build_companion_environment_prompt
from .tool_dispatch import _ToolDispatchContext, available_media_tool_schemas, matched_tool_names
from .turn_errors import emit_conversation_unavailable, emit_llm_error, emit_llm_unavailable, emit_turn_limit
from .turn_inputs import (
    build_turn_inputs,
    memory_query_text,
    merge_session_settings,
    parse_temperature,
    resolve_context_provider_chain,
    resolve_inference_settings,
    user_text_item,
)
from .types import TrackTask

logger = get_logger(__name__)


def _compression_temperature(provider_name: str, settings: dict[str, Any]) -> float:
    normalized = parse_temperature(
        settings.get("chat.compression_temperature"),
        CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    )
    return scale_temperature(provider_name, normalized)


@dataclass(frozen=True)
class ManualCompressionResult:
    """手动压缩结果：info 为 None 表示没有可压缩的历史；用量为压缩后的估算。"""

    info: CompressionInfo | None
    total_tokens: int
    context_window: int


async def compress_session_history(
    conv: Conversation,
    user_id: int,
    session_settings: dict[str, Any],
    client_context: ChatRequestClientContext | None,
) -> ManualCompressionResult:
    """手动压缩会话历史：短会话装配上下文 → 不占会话调用摘要模型 → 短会话写入检查点并重新估算用量；摘要失败抛 CompressionFailedError。调用方负责会话互斥与在途回合校验。"""
    req = ChatRequest(session_id=str(conv.id), message=ChatMessageRequest(content=""))
    memory_scope = conversation_memory_scope(conv, user_id)
    async with session_scope() as db:
        effective_settings = merge_session_settings(await load_user_settings(db, user_id), session_settings, conv=conv)
        inputs = await build_turn_inputs(db, conv, user_id, req, client_context, effective_settings, memory_scope)
    _, info = await compress_history(
        inputs.context,
        client=inputs.client,
        model=inputs.model_name,
        temperature=_compression_temperature(inputs.provider_name, effective_settings),
        language=inputs.language,
        context_length=inputs.ctx_length,
        companion=conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None,
    )
    if info is None:
        return ManualCompressionResult(
            info=None,
            total_tokens=inputs.estimated_tokens,
            context_window=inputs.ctx_length,
        )
    async with session_scope() as db:
        await persist_compression_checkpoint(db, conv.id, info)
        compressed = await build_turn_inputs(db, conv, user_id, req, client_context, effective_settings, memory_scope)
    return ManualCompressionResult(
        info=info,
        total_tokens=compressed.estimated_tokens,
        context_window=compressed.ctx_length,
    )


def _history_unlocked_tool_names(input_items: list[dict]) -> set[str]:
    """历史里调用过的工具，以及经 ``search_tools`` 结果解锁的工具；与派发侧同门控。"""
    unlocked: set[str] = set()
    call_names: dict[str, str] = {}
    for item in input_items:
        if item.get("type") == "function_call" and (name := item.get("name")):
            name = str(name)
            unlocked.add(name)
            if call_id := item.get("call_id"):
                call_names[str(call_id)] = name
        elif item.get("type") == "function_call_output":
            # 只认 search_tools 的结果（与 persistence.py 的解锁门控一致），避免其他工具输出里偶然的 matched_tools 形 JSON 误解锁。
            if call_names.get(str(item.get("call_id") or "")) != "search_tools":
                continue
            unlocked.update(matched_tool_names(item.get("output", "")))
    return unlocked


async def run_chat_turn(
    req: ChatRequest,
    llm_config: UserLlmConfig,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    session_settings: dict | None = None,
    precursor_user_message_ids: list[int] | None = None,
    persisted_message_id: int | None = None,
    final_reply_only: bool = False,
    ephemeral: bool = False,
    headless: bool = False,
    has_viewer: bool = True,
    excluded_tool_names: frozenset[str] = frozenset(),
    max_loop_turns: int | None = None,
    authorization_check: Callable[[], Awaitable[bool]] | None = None,
    turn_timeout_seconds: float | None = None,
    channel_source: ChannelTurnSource | None = None,
) -> None:
    """所有入口共享会话互斥和执行预算；渠道撤权在模型与工具派发边界复核。"""
    async with conversation_lock(req.session_id):
        if authorization_check is not None and not await authorization_check():
            raise asyncio.CancelledError("The channel authorization was revoked")
        timeout = asyncio.timeout(turn_timeout_seconds or SETTINGS.agent_turn_timeout_seconds)
        try:
            async with timeout:
                with generation_timeout_scope(timeout), asset_write_context():
                    await _run_chat_turn(
                        req,
                        llm_config,
                        user_id,
                        emitter,
                        session_client_context,
                        track_task,
                        session_settings=session_settings,
                        precursor_user_message_ids=precursor_user_message_ids,
                        persisted_message_id=persisted_message_id,
                        final_reply_only=final_reply_only,
                        ephemeral=ephemeral,
                        headless=headless,
                        has_viewer=has_viewer,
                        excluded_tool_names=excluded_tool_names,
                        max_loop_turns=max_loop_turns,
                        authorization_check=authorization_check,
                        channel_source=channel_source,
                    )
        except TimeoutError:
            if not timeout.expired():
                raise
            async with session_scope() as db:
                language = await get_user_setting(db, user_id, "language")
            await emitter.send_json(
                {
                    "type": "error",
                    "message": "本次任务已达到运行时限。请核对已执行的操作后再继续。"
                    if language != "en"
                    else "The task reached its time limit. Check completed operations before continuing.",
                },
            )


async def _run_chat_turn(
    req: ChatRequest,
    llm_config: UserLlmConfig,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    session_settings: dict | None = None,
    precursor_user_message_ids: list[int] | None = None,
    persisted_message_id: int | None = None,
    final_reply_only: bool = False,
    ephemeral: bool = False,
    headless: bool = False,
    has_viewer: bool = True,
    excluded_tool_names: frozenset[str] = frozenset(),
    max_loop_turns: int | None = None,
    authorization_check: Callable[[], Awaitable[bool]] | None = None,
    channel_source: ChannelTurnSource | None = None,
) -> None:
    """执行一个对话回合；自动化与回合后整理由会话决定。``ephemeral`` 只用于主动陪伴：内部资料、不落库、可沉默，调用方同时 ``headless``。``has_viewer=False`` 表示帧只被程序捕获（子 Agent 委派）：缓冲交付，不做气泡停顿。"""
    # 默认值运行时解析：工具循环上限可在管理端热调，不能在函数定义期绑定常量。
    if max_loop_turns is None:
        max_loop_turns = SETTINGS.agent_max_loop_turns
    user_message_id: int | None = None
    with ExitStack() as turn_scope:
        # 轮次起点先提交用户输入并解析召回查询；会话退出后生成向量，再以新短会话装配上下文。
        async with session_scope() as db:
            conv = await Conversation.by_session_id(db, req.session_id, user_id=user_id)
            if not conv:
                language = await get_user_setting(db, user_id, "language")
                await emit_conversation_unavailable(emitter, language, "Conversation not found")
                return
            try:
                memory_scope = conversation_memory_scope(conv, user_id)
            except ValueError as exc:
                language = await get_user_setting(db, user_id, "language")
                await emit_conversation_unavailable(emitter, language, str(exc))
                return
            # 主动回合与自动化任务不算用户接触。
            turn_scope.enter_context(user_turn_activity(user_id, enabled=not ephemeral and not conv.is_automation))

            if not ephemeral:
                # 用户行先落库再跑 LLM：失败路径也要把 id 回给活路径，否则撤回/派生一直点不了。
                if persisted_message_id is not None:
                    persisted = await db.get(Message, persisted_message_id)
                    if (
                        persisted is None
                        or persisted.conversation_id != conv.id
                        or persisted.role != "user"
                        or persisted.queued
                        or persisted.discarded
                    ):
                        raise ValueError("Persisted user message does not belong to this turn")
                    user_message_id = persisted_message_id
                else:
                    user_message_id = await _persist_user_message(db, conv.id, req.message)
                await emitter.send_json(
                    {
                        "type": "message.persisted",
                        "role": "user",
                        "message_ids": [*(precursor_user_message_ids or []), user_message_id],
                    },
                )

            # 回合起点重读 user_settings（PUT /api/config 后下一回合即生效）；会话级覆写再覆盖其上，只构建一次供门控与派发共用。
            effective_settings = merge_session_settings(
                await load_user_settings(db, user_id),
                session_settings
                if session_settings is not None
                else safe_json_loads(conv.settings_json or "", default={}),
                conv=conv,
            )
            # 主动回合在此一次加载历史：既取最近用户发言，也传给 build_turn_inputs 免二次读取。
            history: list[Message] | None = None
            memory_query = ""
            if memory_scope is not None:
                if ephemeral:
                    history = await load_context_messages(db, conv)
                memory_query = memory_query_text(req, history or [], use_request=not ephemeral)

        memory_embedding = await embed_memory_text(user_id, memory_query) if len(memory_query.strip()) > 1 else None

        async with session_scope() as db:
            try:
                inputs = await build_turn_inputs(
                    db,
                    conv,
                    user_id,
                    req,
                    session_client_context,
                    effective_settings,
                    memory_scope,
                    proactive_memory_query=memory_query,
                    proactive_memory_embedding=memory_embedding,
                    companion_proactive_turn=ephemeral,
                    excluded_tool_names=excluded_tool_names,
                    history=history,
                )
            except MissingLlmConfigError as exc:
                # 用户行已落库：配置补齐后可按 retry_message_id 重试，不能以没有重试入口的异常收尾；主动回合没有用户行，异常交由调用方记录。
                if user_message_id is None:
                    raise
                logger.warning("LLM turn failed: %s", exc)
                await emit_llm_unavailable(
                    emitter,
                    exc,
                    effective_settings.get("language"),
                    retry_message_id=user_message_id,
                )
                return
            # 本轮尾部资料没有持久化来源，不进入持久摘要。
            runtime_item_start = len(inputs.context["input"])
            waits = (
                await list_companion_intents(db, user_id)
                if conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None
                else []
            )
            if waits:
                inputs.context["input"].append(
                    user_text_item(
                        resolve_prompt_text(PENDING_INTENTIONS_LABELS, inputs.language)
                        + "\n"
                        + json.dumps([wait.model_dump(mode="json") for wait in waits], ensure_ascii=False),
                    ),
                )
            if ephemeral and req.message.content:
                inputs.context["input"].append(user_text_item(req.message.content))
            inputs.context["source_message_ids"].extend([None] * (len(inputs.context["input"]) - runtime_item_start))

        inference = resolve_inference_settings(effective_settings, conv=conv)
        compressed_context = inputs.context
        if (
            not final_reply_only
            and SETTINGS.enable_context_compression
            and effective_settings.get(
                "chat.enable_context_compression",
                True,
            )
            and compression_due(
                inputs.context,
                context_length=inputs.ctx_length,
                threshold_ratio=inference.context_compression_threshold,
                # 尾部资料不在持久基线内，此时按全量估算。
                current_tokens=None if ephemeral or waits else inputs.estimated_tokens,
            )
        ):
            try:
                compressed_context, compress_info = await compress_history(
                    inputs.context,
                    client=inputs.client,
                    model=inputs.model_name,
                    temperature=_compression_temperature(inputs.provider_name, effective_settings),
                    language=inputs.language,
                    context_length=inputs.ctx_length,
                    companion=conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None,
                )
            except CompressionFailedError:
                # 自动压缩失败不阻断本轮，按原上下文继续。
                compress_info = None
            if compress_info is not None:
                async with session_scope() as db:
                    checkpoint = await persist_compression_checkpoint(
                        db,
                        conv.id,
                        compress_info,
                        notify_user_id=user_id if headless else None,
                    )
                # 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补。
                await emitter.send_json(
                    {
                        "type": "compress.completed",
                        "subtype": "compress_summary",
                        "text": checkpoint.content,
                        "message_id": checkpoint.id,
                    },
                )
        # 本轮输入按窗口的四分之一（字符数取 token 数，对中文偏保守）放宽，避免超长粘贴被历史条目的上限截断。
        current_context = truncate_responses_context(
            compressed_context,
            current_max_chars=inputs.ctx_length // 4,
            current_message_id=persisted_message_id if final_reply_only else None,
        )
        # 视频内联在截断之后，只处理幸存者（每请求上限 2 个）；expected_session_id 防 stale 行/跨会话 URL 串台，非法形态降级为 [video]。
        current_context["input"] = await inline_video_parts(current_context["input"], expected_session_id=str(conv.id))

        schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}
        # 继承看压缩/截断前的历史，避免摘要窗口丢掉已解锁工具。
        history_unlocked = _history_unlocked_tool_names(inputs.context["input"])
        initial_tool_names = {"search_tools", "companion_wait"}
        if conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None:
            initial_tool_names.update(COMPANION_MEDIA_TOOL_NAMES)
        active_tool_names = (initial_tool_names | history_unlocked) & set(schemas_by_name)
        turn_reasoning_parts: list[str] = []

        # 固定陪伴会话的终端回复是结构化气泡数组：非流式取得后整体校验再交付。
        companion_reply = conv.kind == SPECIAL_KIND and conv.system_preset_id == COMPANION_PRESET_ID
        async with session_scope() as db:
            media_turn = await load_media_turn(
                db,
                conv,
                structured_reply=companion_reply,
                request=req.message.content,
                retry_after_message_id=persisted_message_id if final_reply_only else None,
            )
            media_turn.source_message_id = user_message_id
            source = await db.get(Message, user_message_id) if user_message_id is not None else None
            media_turn.asset_directory = dated_asset_directory(
                source.created_at if source is not None else utc_now(),
                await resolve_user_timezone(db, user_id) or "UTC",
            )
        dispatch_ctx = _ToolDispatchContext(
            user_id=user_id,
            llm_config=llm_config,
            user_settings=effective_settings,
            session_id=str(conv.id),
            memory_scope=memory_scope,
            native_memory=inputs.native_memory,
            guardrails=ToolCallGuardrailController(),
            emitter=emitter,
            # 子 Agent 回合沿用本回合的无头标志：IM、定时任务等无头回合委派出的本机调用同样不显示桌面工作态。
            delegate_executor=partial(
                run_delegated_turn,
                run_turn=partial(
                    run_chat_turn,
                    headless=headless,
                    authorization_check=authorization_check,
                    channel_source=channel_source,
                ),
                inherited_excluded_tool_names=inputs.excluded_tool_names,
            ),
            headless=headless,
            excluded_tool_names=inputs.excluded_tool_names,
            scene_turn=SceneTurnState(),
            proactive_turn=ephemeral,
            user_message=memory_query if not ephemeral else "",
            media_turn=media_turn,
            authorization_check=authorization_check,
            channel_source=channel_source,
        )

        buffer_text = companion_reply or headless or not has_viewer or conv.kind == IM_KIND or final_reply_only
        delivery = "complete" if companion_reply else "buffered" if buffer_text else "stream"
        if buffer_text:
            await emitter.send_json({"type": "message.start"})
        base_instructions = current_context["instructions"]
        for _ in range(max_loop_turns):
            if authorization_check is not None and not await authorization_check():
                raise asyncio.CancelledError("The channel authorization was revoked")
            async with session_scope() as db:
                await refresh_video_media(db, media_turn)
                if conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None:
                    environment = await build_companion_environment_prompt(db, user_id, language=inputs.language)
                    current_context["instructions"] = base_instructions + "\n\n" + environment

            if not buffer_text:
                await emitter.send_json({"type": "message.start"})
            available_schemas = apply_search_tools_catalog(
                available_media_tool_schemas(list(schemas_by_name.values()), media_turn),
            )
            available_by_name = {schema_name(schema): schema for schema in available_schemas}
            dispatch_ctx = replace(
                dispatch_ctx,
                unavailable_tool_names=frozenset(schemas_by_name.keys() - available_by_name.keys()),
            )
            active_schemas = [available_by_name[name] for name in active_tool_names if name in available_by_name]
            # 供应商链按顺序尝试，仅在流式首事件或完整响应到达前允许回退；每个槽位使用自己的模型与窗口。
            response_started = False

            def set_response_started() -> None:
                nonlocal response_started
                response_started = True

            async def _call(provider: ChatProvider) -> _LLMTurnResult:
                input_length = len(current_context["input"])
                retry_available = True
                reply_format_error = None
                while True:
                    try:
                        return await _generate_llm_response(
                            emitter,
                            provider.config.model,
                            current_context,
                            active_schemas,
                            resolve_context_tokens(provider.provider_name),
                            provider,
                            delivery=delivery,
                            on_response_started=set_response_started,
                            reasoning_effort=inference.reasoning_effort,
                            temperature=inference.temperature,
                            turn_request=req.message.content,
                            user_local_tz=inputs.user_local_tz,
                            lang=inputs.language,
                            speech_config=inputs.speech_config if companion_reply else None,
                            reply_preference=inputs.response_preference if companion_reply else None,
                            reply_persona=inputs.reply_persona,
                            voice_id=inputs.speech_voice,
                            allow_silence=ephemeral and companion_reply,
                            reply_format_error=reply_format_error,
                            media_turn=media_turn,
                            pace_bubbles=has_viewer and not headless,
                            final_reply_only=final_reply_only,
                            allow_voice_fallback=not retry_available,
                        )
                    except _IncompleteResponseError as exc:
                        del current_context["input"][input_length:]
                        if not retry_available or not exc.retryable:
                            raise
                        retry_available = False
                        logger.warning("Retrying incomplete LLM response before text delivery: %s", exc)
                    except _InvalidCompanionReplyError as exc:
                        del current_context["input"][input_length:]
                        if not retry_available:
                            raise
                        retry_available = False
                        reply_format_error = exc
                        logger.warning("Editing final companion reply after format validation failed")

            try:
                async with session_scope() as db:
                    llm_chain = await resolve_context_provider_chain(
                        db,
                        user_id,
                        _reply_repair_history(current_context["input"])
                        if final_reply_only
                        else current_context["input"],
                    )
                llm_result = await execute_with_fallback(
                    llm_chain,
                    ChatProvider,
                    _call,
                    user_id=user_id,
                    stream_started=lambda: response_started,
                )
            except LLMRuntimeError as exc:
                # 链已耗尽或响应开始后失败：补发结尾 error 帧，让渲染端消息状态机干净收尾。
                logger.warning(
                    "LLM turn failed",
                    extra={"user_id": user_id, "reason": exc.classified.reason.value, "error": str(exc)},
                    exc_info=True,
                )
                await emit_llm_error(emitter, exc, inputs.language, retry_message_id=user_message_id)
                break
            except _InvalidCompanionReplyError:
                await emitter.send_json(
                    {
                        "type": "error",
                        "retry_message_id": user_message_id,
                        "message": "本次回复的格式不正确，请重试。"
                        if inputs.language == "zh"
                        else "The reply could not be generated in the required format. Please try again.",
                    },
                )
                break
            except (MissingLlmConfigError, RuntimeError) as exc:
                # 配置缺失或响应未正常完成：结束本轮；完整响应失败也可能发生在请求已开始之后。
                logger.warning("LLM turn failed: %s", exc)
                await emit_llm_unavailable(emitter, exc, inputs.language, retry_message_id=user_message_id)
                break

            if llm_result.reasoning:
                turn_reasoning_parts.append(llm_result.reasoning)

            if not llm_result.tool_calls_list:
                await _persist_assistant_no_tool_turn(
                    conv,
                    user_id,
                    llm_result,
                    emitter=emitter,
                    effective_settings=effective_settings,
                    llm_config=llm_config,
                    user_text=req.message.content,
                    first_user_msg_content=inputs.first_user_msg_content,
                    memory_scope=memory_scope,
                    media=None if companion_reply else media_turn.text_reply_media(),
                    turn_reasoning="\n\n".join(turn_reasoning_parts) or None,
                    persist=not ephemeral,
                    track_task=track_task,
                )
                break

            for tc in llm_result.tool_calls_list:
                name = tc.get("name")
                if isinstance(name, str) and name:
                    active_tool_names.add(name)
            _assign_tool_call_ids(llm_result.tool_calls_list)

            await _persist_assistant_with_tool_calls_and_results(
                conv,
                llm_result,
                dispatch_ctx,
                current_context,
                active_tool_names,
                schemas_by_name,
                persist=not ephemeral,
            )
        else:
            await emit_turn_limit(emitter, inputs.language, max_loop_turns, retry_message_id=user_message_id)
