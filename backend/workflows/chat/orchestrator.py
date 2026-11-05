import json
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal

from features.companion.intents import list_companion_intents
from features.companion.proactive_runtime import user_turn_activity
from features.companion.prompts import PENDING_INTENTIONS_LABELS
from features.configuration.settings.timezone import resolve_user_timezone
from features.configuration.settings.values import get_user_setting, load_user_settings
from features.conversation.ai_routing import conversation_ai_task
from features.conversation.attachments import inline_video_parts
from features.conversation.memory_scope import conversation_memory_scope
from features.conversation.replacement import ReplyReplacement, reply_replacement_target
from features.conversation.replies import CompanionReply, MediaBubble
from features.conversation.reply_media import load_media_turn, refresh_video_media
from features.conversation.request_contracts import ChatMessageRequest, ChatRequest
from features.conversation.snapshots import ConversationSnapshot, MessageSnapshot
from features.conversation.store_contracts import CompressionInfo
from features.conversation.turn_access import context_messages, turn_conversation, turn_message
from features.conversation.turn_store import persist_compression_checkpoint, persist_user_message
from features.media.turn_state import MediaTurnState
from features.memory.memory_retrieval import embed_memory_text
from features.scenes.turn_state import SceneTurnState
from infrastructure.ai.error_classifier import LLMRuntimeError
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.ai.selection import ProviderSelection
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.usage import LlmUsageAccumulator
from infrastructure.assets.asset_store import dated_asset_directory
from infrastructure.assets.write_protection import asset_write_context
from infrastructure.config import SETTINGS
from infrastructure.constants import CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.search import QueryEmbedding
from infrastructure.text import resolve_prompt_text, utc_now
from runtime.client_context import ChatRequestClientContext
from runtime.memory_scope import MemoryScope
from runtime.presets import COMPANION_PRESET_ID
from runtime.tools.domains import apply_search_tools_catalog
from runtime.tools.presentation import VISUAL_TOOL_NAMES
from runtime.tools.registry import REGISTRY, schema_name
from runtime.tools.tool_guardrails import ToolCallGuardrailController
from runtime.turn_ownership import conversation_lock

from workflows.chat.chat_emitter import Emitter
from workflows.chat.context_compressor import CompressionFailedError, compress_history, compression_due
from workflows.chat.delegation import run_delegated_turn
from workflows.chat.message_sanitization import truncate_responses_context
from workflows.chat.model_turn import generate_turn_response
from workflows.chat.persistence import _persist_assistant_no_tool_turn, _persist_assistant_with_tool_calls_and_results
from workflows.chat.prompt_presets import COMPANION_MEDIA_TOOL_NAMES
from workflows.chat.prompts import REPLACE_REPLY_GUIDANCES
from workflows.chat.streaming import _assign_tool_call_ids
from workflows.chat.system_prompt import build_companion_environment_prompt
from workflows.chat.tool_dispatch import _ToolDispatchContext, available_media_tool_schemas, matched_tool_names
from workflows.chat.topic_context import load_topic_context
from workflows.chat.turn_errors import (
    CompanionReplyFormatError,
    emit_conversation_unavailable,
    emit_llm_error,
    emit_llm_unavailable,
)
from workflows.chat.turn_inputs import (
    InferenceSettings,
    TurnInputs,
    build_turn_inputs,
    estimate_context_tokens,
    memory_query_text,
    merge_session_settings,
    parse_temperature,
    resolve_inference_settings,
    user_text_item,
)
from workflows.chat.types import TrackTask
from workflows.companion_actions import build_action_view
from workflows.tool_availability import unavailable_action_tool_names

logger = get_logger(__name__)


class _BufferedReplacementEmitter:
    async def send_json(self, data: dict) -> None:
        """更换回答在终端提交前不发布正文、推理或气泡。"""


def _compression_temperature(settings: dict[str, Any]) -> float:
    return parse_temperature(settings.get("chat.compression_temperature"), CONTEXT_COMPRESSION_TEMPERATURE_DEFAULT)


@dataclass(frozen=True)
class ManualCompressionResult:
    """手动压缩结果：info 为 None 表示没有可压缩的历史；用量为压缩后的估算。"""

    info: CompressionInfo | None
    total_tokens: int
    context_window: int


async def compress_session_history(
    conv: ConversationSnapshot,
    user_id: int,
    session_settings: dict[str, Any],
    client_context: ChatRequestClientContext | None,
) -> ManualCompressionResult:
    """手动压缩会话历史：短会话装配上下文 → 不占会话调用摘要模型 → 短会话写入检查点并重新估算用量；摘要失败抛 CompressionFailedError。调用方负责会话互斥与在途回合校验。"""
    req = ChatRequest(session_id=str(conv.id), message=ChatMessageRequest(content=""))
    memory_scope = conversation_memory_scope(conv, user_id)
    task = conversation_ai_task(conv)
    async with session_scope() as db:
        effective_settings = merge_session_settings(await load_user_settings(db, user_id), session_settings)
        inputs = await build_turn_inputs(
            db, conv, user_id, req, client_context, effective_settings, memory_scope, task=task
        )
    compressed, info = await compress_history(
        inputs.context,
        user_id=user_id,
        task=task,
        temperature=_compression_temperature(effective_settings),
        language=inputs.language,
        context_length=inputs.ctx_length,
        companion=conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None,
    )
    if info is None:
        return ManualCompressionResult(
            info=None, total_tokens=inputs.estimated_tokens, context_window=inputs.ctx_length
        )
    async with session_scope() as db:
        await persist_compression_checkpoint(db, conv.id, info, user_id=user_id)
        history = await context_messages(db, conv)
    return ManualCompressionResult(
        info=info, total_tokens=estimate_context_tokens(compressed, history), context_window=inputs.ctx_length
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
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    task_override: AiTaskKey | None = None,
    session_settings: dict | None = None,
    persisted_message_id: int | None = None,
    final_reply_only: bool = False,
    replace_reply_message_id: int | None = None,
    submission_request_id: str | None = None,
    ephemeral: bool = False,
    headless: bool = False,
    has_viewer: bool = True,
    excluded_tool_names: frozenset[str] = frozenset(),
) -> None:
    """所有入口共享会话互斥和取消收尾；用户请求在受理入口校验授权。``task_override`` 供委派子回合继承父回合任务。"""
    async with conversation_lock(req.session_id):
        with asset_write_context():
            await _run_chat_turn(
                req,
                user_id,
                emitter,
                session_client_context,
                track_task,
                task_override=task_override,
                session_settings=session_settings,
                persisted_message_id=persisted_message_id,
                final_reply_only=final_reply_only,
                replace_reply_message_id=replace_reply_message_id,
                submission_request_id=submission_request_id,
                ephemeral=ephemeral,
                headless=headless,
                has_viewer=has_viewer,
                excluded_tool_names=excluded_tool_names,
            )


@dataclass(frozen=True)
class _TurnSetup:
    """回合起点：会话与任务身份、本轮设置与本轮用户消息。"""

    conv: ConversationSnapshot
    task: AiTaskKey
    memory_scope: MemoryScope | None
    effective_settings: dict[str, Any]
    replacement: ReplyReplacement | None
    user_message_id: int | None
    final_reply_only: bool
    memory_query: str
    history: list[MessageSnapshot] | None


@dataclass(frozen=True)
class _TurnContext:
    """上下文装配阶段的结果：本轮请求上下文、媒体状态与工具派发器。"""

    inputs: TurnInputs
    current_context: dict[str, Any]
    dispatch_ctx: _ToolDispatchContext
    active_tool_names: set[str]
    inference: InferenceSettings
    delivery: Literal["stream", "buffered", "complete"]


async def _load_turn_setup(
    req: ChatRequest,
    user_id: int,
    emitter: Emitter,
    turn_scope: ExitStack,
    *,
    task_override: AiTaskKey | None,
    session_settings: dict | None,
    persisted_message_id: int | None,
    final_reply_only: bool,
    replace_reply_message_id: int | None,
    ephemeral: bool,
) -> _TurnSetup | None:
    """读取会话与作用域，核对已受理输入或保存内部入口的消息；None 表示已发错误帧结束本轮。"""
    user_message_id: int | None = None
    replacement: ReplyReplacement | None = None
    async with session_scope() as db:
        conv = await turn_conversation(db, req.session_id, user_id)
        if not conv:
            language = await get_user_setting(db, user_id, "language")
            await emit_conversation_unavailable(emitter, language, "Conversation not found")
            return
        if replace_reply_message_id is not None:
            replacement = await reply_replacement_target(db, user_id, req.session_id, replace_reply_message_id)
            if persisted_message_id != replacement.source_message_id:
                raise ValueError("Replacement source does not belong to this turn")
            final_reply_only = True
        try:
            memory_scope = conversation_memory_scope(conv, user_id)
        except ValueError as exc:
            language = await get_user_setting(db, user_id, "language")
            await emit_conversation_unavailable(emitter, language, str(exc))
            return
        # 委派子回合继承父回合的任务链；普通入口由会话和主动回合标志决定。
        task = task_override or conversation_ai_task(conv, proactive=ephemeral)
        # 主动回合与自动化任务不算用户接触。
        turn_scope.enter_context(user_turn_activity(user_id, enabled=not ephemeral and not conv.is_automation))

        if not ephemeral:
            # 已持久化输入由调用方发布；本层只发布自己新增的用户行。
            if persisted_message_id is not None:
                persisted = await turn_message(db, persisted_message_id)
                if persisted is None or persisted.conversation_id != conv.id or persisted.role != "user":
                    raise ValueError("Persisted user message does not belong to this turn")
                user_message_id = persisted_message_id
            else:
                user_message_id = await persist_user_message(db, conv.id, req.message, user_id=user_id)
                await emitter.send_json({"type": "message.persisted", "role": "user", "message_ids": [user_message_id]})

        # 回合起点重读 user_settings（PUT /api/config 后下一回合即生效）；会话级覆写再覆盖其上，只构建一次供门控与派发共用。
        effective_settings = merge_session_settings(
            await load_user_settings(db, user_id),
            session_settings if session_settings is not None else (conv.settings_json or {}),
        )
        # 主动回合在此一次加载历史：既取最近用户发言，也传给 build_turn_inputs 免二次读取。
        history: list[MessageSnapshot] | None = None
        if replacement is not None:
            history = [m for m in await context_messages(db, conv) if m.id != replacement.previous.id]
        memory_query = ""
        if memory_scope is not None:
            if ephemeral:
                history = await context_messages(db, conv)
            memory_query = memory_query_text(req, history or [], use_request=not ephemeral)
    return _TurnSetup(
        conv=conv,
        task=task,
        memory_scope=memory_scope,
        effective_settings=effective_settings,
        replacement=replacement,
        user_message_id=user_message_id,
        final_reply_only=final_reply_only,
        memory_query=memory_query,
        history=history,
    )


async def _prepare_turn(
    setup: _TurnSetup,
    req: ChatRequest,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None,
    *,
    ephemeral: bool,
    headless: bool,
    has_viewer: bool,
    persisted_message_id: int | None,
    submission_request_id: str | None,
    excluded_tool_names: frozenset[str],
    memory_embedding: QueryEmbedding | None,
) -> _TurnContext | None:
    """上下文阶段：召回与工具装配、自动压缩、截断与媒体状态；`None` 表示已发错误帧结束本轮。"""
    async with session_scope() as db:
        try:
            inputs = await build_turn_inputs(
                db,
                setup.conv,
                user_id,
                req,
                session_client_context,
                setup.effective_settings,
                setup.memory_scope,
                task=setup.task,
                proactive_memory_query=setup.memory_query,
                proactive_memory_embedding=memory_embedding,
                companion_proactive_turn=ephemeral,
                excluded_tool_names=excluded_tool_names,
                history=setup.history,
            )
        except MissingLlmConfigError as exc:
            # 用户行已落库：配置补齐后可按 retry_message_id 重试，不能以没有重试入口的异常收尾；主动回合没有用户行，异常交由调用方记录。
            if setup.user_message_id is None:
                raise
            logger.warning("LLM turn failed: %s", exc)
            await emit_llm_unavailable(
                emitter, exc, setup.effective_settings.get("language"), retry_message_id=setup.user_message_id
            )
            return
        # 本轮尾部资料没有持久化来源，不进入持久摘要。
        runtime_item_start = len(inputs.context["input"])
        if setup.replacement is not None:
            inputs.context["instructions"] += resolve_prompt_text(REPLACE_REPLY_GUIDANCES, inputs.language)
            inputs.context["input"].append(
                user_text_item(json.dumps({"previous_reply": setup.replacement.previous.content}, ensure_ascii=False))
            )
        waits = (
            await list_companion_intents(db, user_id)
            if setup.conv.system_preset_id == COMPANION_PRESET_ID and setup.conv.parent_id is None
            else []
        )
        if waits:
            inputs.context["input"].append(
                user_text_item(
                    resolve_prompt_text(PENDING_INTENTIONS_LABELS, inputs.language)
                    + "\n"
                    + json.dumps([wait.model_dump(mode="json") for wait in waits], ensure_ascii=False)
                )
            )
        if ephemeral and req.message.content:
            inputs.context["input"].append(user_text_item(req.message.content))
        inputs.context["source_message_ids"].extend([None] * (len(inputs.context["input"]) - runtime_item_start))

    inference = resolve_inference_settings(setup.effective_settings)
    compressed_context = inputs.context
    if (
        not setup.final_reply_only
        and SETTINGS.enable_context_compression
        and setup.effective_settings.get("chat.enable_context_compression", True)
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
                user_id=user_id,
                task=setup.task,
                temperature=_compression_temperature(setup.effective_settings),
                language=inputs.language,
                context_length=inputs.ctx_length,
                companion=setup.conv.system_preset_id == COMPANION_PRESET_ID and setup.conv.parent_id is None,
            )
        except CompressionFailedError:
            # 自动压缩失败不阻断本轮，按原上下文继续。
            compress_info = None
        if compress_info is not None:
            async with session_scope() as db:
                checkpoint = await persist_compression_checkpoint(
                    db, setup.conv.id, compress_info, user_id=user_id, notify_user_id=user_id if headless else None
                )
            # 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补。
            await emitter.send_json(
                {
                    "type": "compress.completed",
                    "subtype": checkpoint.subtype,
                    "text": checkpoint.content,
                    "message_id": checkpoint.id,
                }
            )
    # 本轮输入按窗口的四分之一（字符数取 token 数，对中文偏保守）放宽，避免超长粘贴被历史条目的上限截断。
    current_context = truncate_responses_context(
        compressed_context,
        current_max_chars=inputs.ctx_length // 4,
        current_message_id=persisted_message_id if setup.final_reply_only else None,
    )
    # 视频内联在截断之后，只处理幸存者（每请求上限 2 个）；expected_session_id 防 stale 行/跨会话 URL 串台，非法形态降级为 [video]。
    current_context["input"] = await inline_video_parts(
        current_context["input"], expected_session_id=str(setup.conv.id)
    )

    schemas_by_name: dict[str, dict] = {schema_name(s): s for s in inputs.all_schemas}
    # 继承看压缩/截断前的历史，避免摘要窗口丢掉已解锁工具。
    history_unlocked = _history_unlocked_tool_names(inputs.context["input"])
    initial_tool_names = {"search_tools", "companion_wait"}
    if setup.conv.system_preset_id == COMPANION_PRESET_ID and setup.conv.parent_id is None:
        initial_tool_names.update(COMPANION_MEDIA_TOOL_NAMES)
    active_tool_names = (initial_tool_names | history_unlocked) & set(schemas_by_name)

    # 陪伴用户会话的终端回复是结构化气泡数组：非流式取得后整体校验再交付。
    companion_reply = setup.conv.system_preset_id == COMPANION_PRESET_ID and setup.conv.parent_id is None
    async with session_scope() as db:
        media_turn = await load_media_turn(
            db,
            setup.conv,
            task=setup.task.value,
            structured_reply=companion_reply,
            request=req.message.content,
            source_message_id=setup.user_message_id,
            request_scope=f"user:{setup.user_message_id}"
            if setup.user_message_id is not None
            else submission_request_id,
            retry_after_message_id=persisted_message_id if setup.final_reply_only else None,
        )
        media_turn.source_message_id = setup.user_message_id
        if setup.replacement is not None and setup.replacement.previous.reply_json:
            old_reply = CompanionReply.from_saved_snapshot(
                setup.replacement.previous.reply_json, setup.replacement.previous.content or ""
            )
            retained_ids = {b.media_id for b in old_reply.bubbles if isinstance(b, MediaBubble)}
            media_turn.artifacts = {key: value for key, value in media_turn.artifacts.items() if key in retained_ids}
            media_turn.required_goals.clear()
            media_turn.current_versions.clear()
            for bubble in old_reply.bubbles:
                if not isinstance(bubble, MediaBubble):
                    continue
                artifact = media_turn.artifacts[bubble.media_id]
                artifact.goal_id = bubble.goal_id
                if artifact.bound_message_id == setup.replacement.previous.id:
                    artifact.bound_message_id = None
                media_turn.required_goals.add(bubble.goal_id)
                media_turn.current_versions[bubble.goal_id] = bubble.media_id
        source = await turn_message(db, setup.user_message_id) if setup.user_message_id is not None else None
        media_turn.asset_directory = dated_asset_directory(
            source.created_at if source is not None else utc_now(),
            await resolve_user_timezone(db, user_id) or "UTC",
        )
    dispatch_ctx = _ToolDispatchContext(
        user_id=user_id,
        llm_config=inputs.llm_config,
        user_settings=setup.effective_settings,
        session_id=str(setup.conv.id),
        memory_scope=setup.memory_scope,
        native_memory=inputs.native_memory,
        guardrails=ToolCallGuardrailController(),
        emitter=emitter,
        # 子 Agent 回合沿用本回合的无头标志：定时任务等无头回合委派出的本机调用同样不显示桌面工作态。
        delegate_executor=partial(
            run_delegated_turn,
            user_id=user_id,
            run_turn=partial(run_chat_turn, headless=headless),
            task=setup.task,
            inherited_excluded_tool_names=inputs.excluded_tool_names,
        ),
        headless=headless,
        excluded_tool_names=inputs.excluded_tool_names,
        scene_turn=SceneTurnState(),
        proactive_turn=ephemeral,
        user_message=setup.memory_query if not ephemeral else "",
        media_turn=media_turn,
    )

    buffer_text = companion_reply or headless or not has_viewer or setup.final_reply_only
    delivery = "complete" if companion_reply else "buffered" if buffer_text else "stream"
    if buffer_text:
        await emitter.send_json({"type": "message.start"})
    return _TurnContext(
        inputs=inputs,
        current_context=current_context,
        dispatch_ctx=dispatch_ctx,
        active_tool_names=active_tool_names,
        inference=inference,
        delivery=delivery,
    )


async def _refresh_turn_material(
    conv: ConversationSnapshot,
    user_id: int,
    inputs: TurnInputs,
    current_context: dict[str, Any],
    *,
    base_instructions: str,
    memory_scope: MemoryScope | None,
    memory_query: str,
    replacement: ReplyReplacement | None,
    media_turn: MediaTurnState,
) -> frozenset[str]:
    """每轮模型调用前的资料刷新：动作与环境、话题卡片、视频媒体；返回暂不可用的动作工具。"""
    instruction_parts = [base_instructions]
    action_tools_not_ready: frozenset[str] = frozenset()
    action_view = None
    async with session_scope() as db:
        await refresh_video_media(db, media_turn)
        if replacement is not None:
            for artifact in media_turn.artifacts.values():
                if artifact.bound_message_id == replacement.previous.id:
                    artifact.bound_message_id = None
        if conv.system_preset_id == COMPANION_PRESET_ID and conv.parent_id is None:
            action_view = await build_action_view(user_id)
            environment = await build_companion_environment_prompt(
                db, user_id, language=inputs.language, action_view=action_view
            )
            instruction_parts.append(environment)
            action_tools_not_ready = await unavailable_action_tool_names(user_id, action_view=action_view)
            if memory_scope is not None:
                topic_context_at = utc_now()
                topic_material, topic_cards = await load_topic_context(
                    db,
                    memory_scope,
                    inputs.context.get("historical_topic_ids", ()),
                    now=topic_context_at,
                    query=memory_query,
                )
                current_context["topic_material"] = topic_material
                current_context["topic_cards"] = topic_cards
                current_context["topic_context_at"] = topic_context_at
    current_context["instructions"] = "\n\n".join(instruction_parts)
    return action_tools_not_ready


async def _run_chat_turn(
    req: ChatRequest,
    user_id: int,
    emitter: Emitter,
    session_client_context: ChatRequestClientContext | None = None,
    track_task: TrackTask | None = None,
    *,
    task_override: AiTaskKey | None = None,
    session_settings: dict | None = None,
    persisted_message_id: int | None = None,
    final_reply_only: bool = False,
    replace_reply_message_id: int | None = None,
    submission_request_id: str | None = None,
    ephemeral: bool = False,
    headless: bool = False,
    has_viewer: bool = True,
    excluded_tool_names: frozenset[str] = frozenset(),
) -> None:
    """执行一个对话回合；自动化与回合后整理由会话决定。``ephemeral`` 只用于主动陪伴：内部资料、不落库、可沉默，调用方同时 ``headless``。``has_viewer=False`` 表示帧只被程序捕获（子 Agent 委派）：缓冲交付，不做气泡停顿。"""
    with ExitStack() as turn_scope:
        setup = await _load_turn_setup(
            req,
            user_id,
            emitter,
            turn_scope,
            task_override=task_override,
            session_settings=session_settings,
            persisted_message_id=persisted_message_id,
            final_reply_only=final_reply_only,
            replace_reply_message_id=replace_reply_message_id,
            ephemeral=ephemeral,
        )
        if setup is None:
            return
        memory_embedding = (
            await embed_memory_text(user_id, setup.memory_query) if len(setup.memory_query.strip()) > 1 else None
        )
        turn = await _prepare_turn(
            setup,
            req,
            user_id,
            emitter,
            session_client_context,
            ephemeral=ephemeral,
            headless=headless,
            has_viewer=has_viewer,
            persisted_message_id=persisted_message_id,
            submission_request_id=submission_request_id,
            excluded_tool_names=excluded_tool_names,
            memory_embedding=memory_embedding,
        )
        if turn is None:
            return
        inputs, current_context = turn.inputs, turn.current_context
        dispatch_ctx, active_tool_names = turn.dispatch_ctx, turn.active_tool_names
        media_turn = dispatch_ctx.media_turn
        generation_emitter = _BufferedReplacementEmitter() if setup.replacement is not None else emitter
        turn_reasoning_parts: list[str] = []
        base_instructions = current_context["instructions"]
        audio_transcripts: dict[str, str | Exception] = {}
        turn_usage = LlmUsageAccumulator()
        while True:
            action_tools_not_ready = await _refresh_turn_material(
                setup.conv,
                user_id,
                inputs,
                current_context,
                base_instructions=base_instructions,
                memory_scope=setup.memory_scope,
                memory_query=setup.memory_query,
                replacement=setup.replacement,
                media_turn=media_turn,
            )

            if turn.delivery == "stream":
                await emitter.send_json({"type": "message.start"})
            # 动作资料与工具资格共用本次快照，执行时再校验目标仍有效。
            schemas_by_name = {
                schema_name(schema): schema
                for schema in REGISTRY.get_all_schemas(
                    user_id, user_settings=setup.effective_settings, exclude=inputs.excluded_tool_names
                )
            }
            available_schemas = apply_search_tools_catalog(
                available_media_tool_schemas(
                    [schema for name, schema in schemas_by_name.items() if name not in action_tools_not_ready],
                    media_turn,
                )
            )
            available_by_name = {schema_name(schema): schema for schema in available_schemas}
            if media_turn.structured_reply:
                active_tool_names.update(VISUAL_TOOL_NAMES & available_by_name.keys())
            dispatch_ctx = replace(
                dispatch_ctx, unavailable_tool_names=frozenset(schemas_by_name.keys() - available_by_name.keys())
            )
            active_schemas = [available_by_name[name] for name in active_tool_names if name in available_by_name]
            # 只依据已有结果收束回复；主动回合由意图有效期约束。
            reply_only = (
                setup.final_reply_only
                or (media_turn.structured_reply and media_turn.final_reply_due())
                or (not ephemeral and dispatch_ctx.guardrails.must_conclude)
            )
            try:
                llm_result = await generate_turn_response(
                    generation_emitter,
                    user_id=user_id,
                    session_id=str(setup.conv.id),
                    task=setup.task,
                    context=current_context,
                    inputs=inputs,
                    inference=turn.inference,
                    media_turn=media_turn,
                    schemas=active_schemas,
                    request=req.message.content,
                    delivery=turn.delivery,
                    allow_silence=ephemeral and media_turn.structured_reply,
                    pace_bubbles=has_viewer and not headless and setup.replacement is None,
                    final_reply_only=reply_only,
                    selection=ProviderSelection(origin=setup.replacement.generation_origin, mode="next")
                    if setup.replacement
                    else None,
                    transcripts=audio_transcripts,
                    usage=turn_usage,
                )
            except LLMRuntimeError as exc:
                logger.warning(
                    "LLM turn failed",
                    extra={"user_id": user_id, "reason": exc.classified.reason.value, "error": str(exc)},
                    exc_info=True,
                )
                await emit_llm_error(emitter, exc, inputs.language, retry_message_id=setup.user_message_id)
                break
            except CompanionReplyFormatError:
                await emitter.send_json(
                    {
                        "type": "error",
                        "retry_message_id": setup.user_message_id,
                        "message": "本次回复的格式不正确，请重试。"
                        if inputs.language == "zh"
                        else "The reply could not be generated in the required format. Please try again.",
                    }
                )
                break
            except (MissingLlmConfigError, RuntimeError) as exc:
                logger.warning("LLM turn failed: %s", exc)
                await emit_llm_unavailable(emitter, exc, inputs.language, retry_message_id=setup.user_message_id)
                break

            if llm_result.reasoning:
                turn_reasoning_parts.append(llm_result.reasoning)

            if not llm_result.tool_calls_list:
                await _persist_assistant_no_tool_turn(
                    setup.conv,
                    user_id,
                    llm_result,
                    task=setup.task,
                    emitter=emitter,
                    effective_settings=setup.effective_settings,
                    user_text=req.message.content,
                    first_user_msg_content=inputs.first_user_msg_content,
                    memory_scope=setup.memory_scope,
                    media=None if media_turn.structured_reply else media_turn.text_reply_media(),
                    turn_reasoning="\n\n".join(turn_reasoning_parts) or None,
                    cache_usage=turn_usage.summary(),
                    persist=not ephemeral,
                    track_task=track_task,
                    replace_reply_message_id=replace_reply_message_id,
                    submission_request_id=submission_request_id,
                    media_request_scope=media_turn.request_scope,
                )
                break

            for tc in llm_result.tool_calls_list:
                name = tc.get("name")
                if isinstance(name, str) and name:
                    active_tool_names.add(name)
            _assign_tool_call_ids(llm_result.tool_calls_list)
            await _persist_assistant_with_tool_calls_and_results(
                setup.conv,
                llm_result,
                dispatch_ctx,
                current_context,
                active_tool_names,
                schemas_by_name,
                persist=not ephemeral,
            )
