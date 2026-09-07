from components import (
    AGENT_MAX_LOOP_TURNS,
    CHAT_TEMPERATURE_DEFAULT,
    CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT,
    DEFAULT_LANGUAGE,
    SETTINGS,
    get_logger,
    safe_json_loads,
    session_scope,
)
from modules.auth import ChatRequestClientContext
from modules.conversation import Conversation, Message
from modules.system import ChatRequest

from services.media import inline_video_parts, prune_videos_in_range
from services.tools import ToolCallGuardrailController, schema_name

from ..llm import (
    LLMRuntimeError,
    MissingLlmConfigError,
    ServiceType,
    compress_history_if_needed,
    execute_with_fallback,
    resolve_context_tokens,
    scale_temperature,
)
from .chat_emitter import Emitter
from .message_sanitization import truncate_responses_context
from .persistence import (
    _persist_assistant_no_tool_turn,
    _persist_assistant_with_tool_calls_and_results,
    _persist_user_message,
)
from .streaming import _emit_llm_error, _ensure_tool_call_ids, _stream_llm_response
from .tool_dispatch import _ToolDispatchContext
from .turn_inputs import (
    _parse_reasoning_effort,
    build_turn_inputs,
    load_user_settings,
    merge_session_settings,
    parse_temperature,
)
from .types import IterationBudget, TrackTask

logger = get_logger(__name__)


def _extract_unlocked_tool_names_from_context(input_items: list[dict]) -> set[str]:
    unlocked: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and (name := item.get("name")):
            unlocked.add(str(name))
        elif isinstance(item.get("tool_calls"), list):
            for tc in item["tool_calls"]:
                if isinstance(tc, dict) and (t_name := tc.get("name")):
                    unlocked.add(str(t_name))
        if item.get("type") == "function_call_output":
            raw_output = item.get("output", "")
            parsed = safe_json_loads(raw_output) if isinstance(raw_output, str) else raw_output
            if isinstance(parsed, dict) and isinstance(parsed.get("matched_tools"), list):
                for t in parsed["matched_tools"]:
                    if isinstance(t, dict) and (t_name := t.get("name")):
                        unlocked.add(str(t_name))
    return unlocked


async def run_chat_turn(
    req: ChatRequest,
    llm_config: dict,
    user_settings: dict,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    session_settings: dict | None = None,
    precursor_user_message_ids: list[int] | None = None,
) -> None:
    # 轮次起始是唯一的多读阶段，集中在一个短 session 内完成；之后每次 DB 访问都开新 session，避免跨多秒 LLM 等待持有连接。
    async with session_scope() as db:
        conv = await Conversation.by_session_id(db, req.session_id, user_id=user_id)
        if not conv:
            await emitter.send_json({"type": "error", "message": "Conversation not found"})
            return
        sid = str(conv.id)

        # 用户行先落库再跑 LLM：失败路径也要把 id 回给活路径，否则撤回/派生一直点不了。
        user_message_id = await _persist_user_message(db, conv, req)
        await emitter.send_json(
            {
                "type": "message.persisted",
                "role": "user",
                "message_ids": [*(precursor_user_message_ids or []), user_message_id],
            },
        )

        # 回合起点重读 user_settings：PUT /api/config（工具集开关、语言等）后无需重连 WS 下一回合即生效；
        # 入口侧传入的快照仅作签名兼容保留。会话级覆写再覆盖其上，仍仅构建一次并被注册表门控和工具派发共用。
        effective_settings = merge_session_settings(await load_user_settings(db, user_id), session_settings)
        inputs = await build_turn_inputs(db, conv, user_id, req, session_client_context, effective_settings)

    compression_enabled = safe_json_loads(effective_settings.get("chat.enable_context_compression", ""), default=SETTINGS.enable_context_compression)
    compression_threshold = safe_json_loads(effective_settings.get("chat.context_compression_threshold", ""), default=SETTINGS.context_compression_threshold)
    compression_u = parse_temperature(effective_settings.get("chat.compression_temperature"), CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT)
    reasoning_effort = _parse_reasoning_effort(effective_settings.get("agent.reasoning_effort") or effective_settings.get("reasoning_effort"))
    temperature = parse_temperature(effective_settings.get("agent.temperature"), CHAT_TEMPERATURE_DEFAULT)
    compressed_context, compress_info = await compress_history_if_needed(
        inputs.context,
        client=inputs.client,
        model=inputs.model_name,
        context_length=inputs.ctx_length,
        enabled=compression_enabled,
        threshold_ratio=compression_threshold,
        temperature=scale_temperature(inputs.provider_name, compression_u),
        language=effective_settings.get("language", DEFAULT_LANGUAGE),
        current_tokens=inputs.estimated_tokens,
    )
    # 持久化压缩检查点，使下一轮历史重建从此开始读取；被压缩的消息仍留在 DB，但不再进入 LLM 读路径。对所有会话类型均生效。
    if compress_info is not None:
        async with session_scope() as db:
            checkpoint = Message(
                conversation_id=conv.id,
                role="system",
                content=f"[🗜️ 对话压缩 — {compress_info['replaced_count']} 条早期消息已压缩]\n{compress_info['summary']}",
                subtype="compress_summary",
                prompt_tokens=compress_info.get("prompt_tokens", 0),
                completion_tokens=compress_info.get("completion_tokens", 0),
            )
            db.add(checkpoint)
            await db.commit()
            checkpoint_id = checkpoint.id
            # 检查点之前的视频不会再进读路径，磁盘是死重量；清理并改写历史行 part。
            await prune_videos_in_range(db, conv.id, hi=checkpoint_id)
            await db.commit()
        # 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补。
        await emitter.send_json(
            {
                "type": "compress.completed",
                "subtype": "compress_summary",
                "text": checkpoint.content,
                "message_id": checkpoint_id,
            },
        )
    current_context = truncate_responses_context(compressed_context)
    # 视频内联在截断之后：窗口外的老视频已被占位替换，内联只处理幸存者（每请求上限 2 个）。
    # expected_session_id 防 stale DB 行 / 跨会话 URL 串到当前会话：跨会话或非法形态一律降级为 [video]。
    current_context["input"] = await inline_video_parts(current_context["input"], expected_session_id=str(conv.id))

    guardrails = ToolCallGuardrailController()
    budget = IterationBudget(max_total=AGENT_MAX_LOOP_TURNS)
    schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}
    # 继承看压缩/截断前的历史，避免摘要窗口丢掉已解锁工具。
    raw_items = inputs.context.get("input") or []
    history_unlocked = _extract_unlocked_tool_names_from_context(raw_items if isinstance(raw_items, list) else [])
    active_tool_names: set[str] = {"search_tools"} | (history_unlocked & set(schemas_by_name))
    # 本轮所有工具批次产出的生成媒体，随终端 assistant 行落库并在 message.complete 下发。
    turn_media: list[dict[str, str]] = []
    turn_reasoning_parts: list[str] = []

    dispatch_ctx = _ToolDispatchContext(
        user_id=user_id,
        llm_config=llm_config,
        user_settings=effective_settings,
        session_id=sid,
        native_memory=inputs.native_memory,
        guardrails=guardrails,
        emitter=emitter,
    )

    while True:
        if not budget.consume():
            await emitter.send_json(
                {"type": "error", "message": f"Max tool execution turns ({AGENT_MAX_LOOP_TURNS}) reached. Terminating loop to prevent unbounded execution."},
            )
            break

        active_schemas = [schemas_by_name[n] for n in active_tool_names if n in schemas_by_name]
        # 供应商链包装：按顺序尝试已配置供应商，仅在尚未输出 chunk 时触发回退；每次尝试使用对应槽位的 model，避免回退供应商收到不识别的模型名导致 model_not_found、链提前耗尽。
        stream_emitted = False

        async def _call(provider):
            if provider.raw_client() is None:
                raise RuntimeError(f"provider {provider.provider_name} does not expose the Responses API")
            model_for_slot = inputs.model_override or provider.config.model
            # 渲染端钉住的窗口优先；否则按供应商重新解析，使回退供应商更小的默认窗口生效。
            slot_ctx_length = inputs.ctx_length if inputs.context_tokens_override is not None else resolve_context_tokens(provider.provider_name, ServiceType.llm)
            return await _stream_llm_response(
                emitter,
                model_for_slot,
                current_context,
                active_schemas,
                slot_ctx_length,
                provider,
                on_first_chunk=set_stream_emitted,
                reasoning_effort=reasoning_effort,
                temperature=temperature,
                allowed_emotions=inputs.allowed_emotions,
                allowed_actions=inputs.allowed_actions,
                user_local_tz=inputs.user_local_tz,
                lang=inputs.language,
            )

        def set_stream_emitted() -> None:
            nonlocal stream_emitted
            stream_emitted = True

        try:
            # db=None：链已在上方预解析，流式调用与回退期间不持有 session。
            llm_result = await execute_with_fallback(None, user_id, "llm", call_fn=_call, stream_started=lambda: stream_emitted, _chain=inputs.llm_chain)
        except LLMRuntimeError as exc:
            # 链已耗尽（非回退错误或已输出 chunk 后中断）：补发结尾 error 帧，让渲染端消息状态机干净收尾。
            reason_val = exc.classified.reason.value if getattr(exc, "classified", None) else "unknown"
            prov_val = getattr(getattr(exc, "classified", None), "provider", None)
            model_val = getattr(getattr(exc, "classified", None), "model", None)
            logger.warning("LLM turn failed", extra={"user_id": user_id, "reason": reason_val, "provider": prov_val, "model": model_val, "error": str(exc)}, exc_info=True)
            await _emit_llm_error(emitter, exc)
            break
        except (MissingLlmConfigError, RuntimeError) as exc:
            # 空链或槽位供应商未暴露 Responses API：仅在无回退时派发器才暴露此类错误，输出定制化错误并结束本轮。
            logger.warning("LLM chain failed to start: %s", exc)
            await emitter.send_json({"type": "error", "message": f"LLM unavailable: {exc}"})
            break

        if llm_result.reasoning:
            turn_reasoning_parts.append(llm_result.reasoning)

        if not llm_result.tool_calls_list:
            await _persist_assistant_no_tool_turn(
                conv,
                user_id,
                effective_settings,
                emitter,
                req,
                llm_result.turn_content,
                llm_result.final_prompt_tokens,
                llm_result.final_completion_tokens,
                llm_result.final_usage_payload,
                llm_result.turn_duration_ms,
                llm_config,
                inputs.first_user_msg_content,
                current_context,
                track_task,
                provider_name=inputs.provider_name,
                emotion=llm_result.emotion,
                actions=llm_result.actions,
                spatial_locale=llm_result.spatial_locale,
                spatial_target=llm_result.spatial_target,
                mood=llm_result.mood,
                media=turn_media,
                reasoning=llm_result.reasoning,
                turn_reasoning="\n\n".join(turn_reasoning_parts) or None,
            )
            break

        for tc in llm_result.tool_calls_list:
            name = tc.get("name")
            if isinstance(name, str) and name:
                active_tool_names.add(name)
        _ensure_tool_call_ids(llm_result.tool_calls_list)

        turn_media.extend(
            await _persist_assistant_with_tool_calls_and_results(
                conv,
                llm_result.tool_calls_list,
                llm_result.turn_content,
                llm_result.final_prompt_tokens,
                llm_result.final_completion_tokens,
                llm_result.turn_duration_ms,
                dispatch_ctx,
                current_context,
                active_tool_names,
                schemas_by_name,
                reasoning=llm_result.reasoning,
            ),
        )

        if guardrails.halt_decision:
            await emitter.send_json({"type": "error", "message": f"Tool execution loop halted by guardrails: {guardrails.halt_decision.message}"})
            break
