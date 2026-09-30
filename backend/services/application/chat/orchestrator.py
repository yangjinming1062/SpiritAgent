import json
from contextlib import ExitStack
from functools import partial

from components import (
    CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    SETTINGS,
    get_logger,
    resolve_prompt_text,
    safe_json_loads,
    session_scope,
)
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation, Message
from modules.settings import load_user_settings
from modules.system import ChatRequest
from prompts.companion import PENDING_INTENTIONS_LABELS

from services.contracts import SceneTurnState
from services.domains.companion import list_companion_intents, user_turn_activity
from services.domains.conversation import (
    DEFAULT_PRESET_ID,
    IM_KIND,
    SPECIAL_KIND,
    conversation_memory_scope,
    load_media_turn,
    refresh_video_media,
)
from services.domains.media import inline_video_parts
from services.domains.memory import embed_memory_text
from services.infrastructure.llm import (
    ChatProvider,
    LLMRuntimeError,
    MissingLlmConfigError,
    UserLlmConfig,
    execute_with_fallback,
    resolve_context_tokens,
    scale_temperature,
)
from services.infrastructure.tool_runtime import ToolCallGuardrailController, schema_name

from .chat_emitter import Emitter
from .context_compressor import compress_history, compression_due
from .delegation import run_delegated_turn
from .message_sanitization import truncate_responses_context
from .persistence import (
    _persist_assistant_no_tool_turn,
    _persist_assistant_with_tool_calls_and_results,
    _persist_user_message,
    persist_compression_checkpoint,
)
from .streaming import (
    _assign_tool_call_ids,
    _emit_llm_error,
    _generate_llm_response,
    _IncompleteResponseError,
    _InvalidCompanionReplyError,
    _LLMTurnResult,
)
from .system_prompt import build_companion_environment_prompt
from .tool_dispatch import _ToolDispatchContext, matched_tool_names
from .turn_inputs import (
    build_turn_inputs,
    load_memory_query_text,
    merge_session_settings,
    parse_temperature,
    resolve_inference_settings,
    user_text_item,
)
from .types import TrackTask

logger = get_logger(__name__)


def _history_unlocked_tool_names(input_items: list[dict]) -> set[str]:
    """历史里调用过或经 ``search_tools`` 解锁的工具。"""
    unlocked: set[str] = set()
    for item in input_items:
        if item.get("type") == "function_call" and (name := item.get("name")):
            unlocked.add(str(name))
        elif item.get("type") == "function_call_output":
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
    ephemeral: bool = False,
    headless: bool = False,
    excluded_tool_names: frozenset[str] = frozenset(),
    max_loop_turns: int | None = None,
) -> None:
    """执行一个对话回合；自动化与回合后整理由会话决定。``ephemeral`` 只用于主动陪伴：内部资料、不落库、可沉默，调用方同时 ``headless``。"""
    # 默认值运行时解析：工具循环上限可在管理端热调，不能在函数定义期绑定常量。
    if max_loop_turns is None:
        max_loop_turns = SETTINGS.agent_max_loop_turns
    with ExitStack() as turn_scope:
        # 轮次起点先提交用户输入并解析召回查询；会话退出后生成向量，再以新短会话装配上下文。
        async with session_scope() as db:
            conv = await Conversation.by_session_id(db, req.session_id, user_id=user_id)
            if not conv:
                await emitter.send_json({"type": "error", "message": "Conversation not found"})
                return
            try:
                memory_scope = conversation_memory_scope(conv, user_id)
            except ValueError as exc:
                await emitter.send_json({"type": "error", "message": str(exc)})
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
            memory_query = (
                await load_memory_query_text(db, conv, req, use_request=not ephemeral)
                if memory_scope is not None
                else ""
            )

        memory_embedding = await embed_memory_text(user_id, memory_query) if len(memory_query.strip()) > 1 else None

        async with session_scope() as db:
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
            )
            # 本轮尾部资料没有持久化来源，不进入持久摘要。
            runtime_item_start = len(inputs.context["input"])
            waits = await list_companion_intents(db, user_id) if conv.system_preset_id == DEFAULT_PRESET_ID else []
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
        if effective_settings.get(
            "chat.enable_context_compression",
            SETTINGS.enable_context_compression,
        ) and compression_due(
            inputs.context,
            context_length=inputs.ctx_length,
            threshold_ratio=inference.context_compression_threshold,
            # 尾部资料不在持久基线内，此时按全量估算。
            current_tokens=None if ephemeral or waits else inputs.estimated_tokens,
        ):
            compressed_context, compress_info = await compress_history(
                inputs.context,
                client=inputs.client,
                model=inputs.model_name,
                temperature=scale_temperature(
                    inputs.provider_name,
                    parse_temperature(
                        effective_settings.get("chat.compression_temperature"),
                        CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
                    ),
                ),
                language=inputs.language,
            )
            if compress_info is not None and not ephemeral:
                async with session_scope() as db:
                    checkpoint = await persist_compression_checkpoint(db, conv.id, compress_info)
                # 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补。
                await emitter.send_json(
                    {
                        "type": "compress.completed",
                        "subtype": "compress_summary",
                        "text": checkpoint.content,
                        "message_id": checkpoint.id,
                    },
                )
        current_context = truncate_responses_context(compressed_context)
        # 视频内联在截断之后，只处理幸存者（每请求上限 2 个）；expected_session_id 防 stale 行/跨会话 URL 串台，非法形态降级为 [video]。
        current_context["input"] = await inline_video_parts(current_context["input"], expected_session_id=str(conv.id))

        schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}
        # 继承看压缩/截断前的历史，避免摘要窗口丢掉已解锁工具。
        history_unlocked = _history_unlocked_tool_names(inputs.context["input"])
        active_tool_names = ({"search_tools", "companion_wait"} | history_unlocked) & set(schemas_by_name)
        turn_reasoning_parts: list[str] = []

        # 固定陪伴会话的终端回复是结构化气泡数组：非流式取得后整体校验再交付。
        companion_reply = conv.kind == SPECIAL_KIND and conv.system_preset_id == DEFAULT_PRESET_ID
        async with session_scope() as db:
            media_turn = await load_media_turn(db, conv, structured_reply=companion_reply, request=req.message.content)
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
            delegate_executor=partial(run_delegated_turn, run_turn=partial(run_chat_turn, headless=headless)),
            headless=headless,
            excluded_tool_names=inputs.excluded_tool_names,
            scene_turn=SceneTurnState(),
            media_turn=media_turn,
        )

        buffer_text = companion_reply or headless or conv.kind == IM_KIND
        delivery = "complete" if companion_reply else "buffered" if buffer_text else "stream"
        if buffer_text:
            await emitter.send_json({"type": "message.start"})
        base_instructions = current_context["instructions"]
        for _ in range(max_loop_turns):
            async with session_scope() as db:
                await refresh_video_media(db, media_turn)
                if conv.system_preset_id == DEFAULT_PRESET_ID:
                    environment = await build_companion_environment_prompt(db, user_id, language=inputs.language)
                    current_context["instructions"] = base_instructions + "\n\n" + environment

            if not buffer_text:
                await emitter.send_json({"type": "message.start"})
            active_schemas = [schemas_by_name[n] for n in active_tool_names if n in schemas_by_name]
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
                            user_local_tz=inputs.user_local_tz,
                            lang=inputs.language,
                            speech_config=inputs.speech_config if companion_reply else None,
                            reply_preference=inputs.response_preference if companion_reply else None,
                            voice_id=inputs.speech_voice,
                            allow_silence=ephemeral and companion_reply,
                            reply_format_error=reply_format_error,
                            media_turn=media_turn,
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
                        logger.warning("Retrying final companion reply after format validation failed")

            try:
                llm_result = await execute_with_fallback(
                    inputs.llm_chain,
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
                await _emit_llm_error(emitter, exc)
                break
            except _InvalidCompanionReplyError:
                await emitter.send_json(
                    {
                        "type": "error",
                        "message": "本次回复的格式不正确，请重试。"
                        if inputs.language == "zh"
                        else "The reply could not be generated in the required format. Please try again.",
                    },
                )
                break
            except (MissingLlmConfigError, RuntimeError) as exc:
                # 配置缺失或响应未正常完成：结束本轮；完整响应失败也可能发生在请求已开始之后。
                logger.warning("LLM turn failed: %s", exc)
                await emitter.send_json({"type": "error", "message": f"LLM unavailable: {exc}"})
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
                    provider_name=inputs.provider_name,
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
            await emitter.send_json(
                {
                    "type": "error",
                    "message": f"Max tool execution turns ({max_loop_turns}) reached. Terminating loop to prevent unbounded execution.",
                },
            )
