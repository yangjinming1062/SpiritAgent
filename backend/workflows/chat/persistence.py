import asyncio
import json
from collections.abc import Coroutine
from dataclasses import asdict
from typing import Any

from features.companion.mood import update_mood_from_companion_turn
from features.conversation.audio_input import ensure_audio_transcripts
from features.conversation.history import build_session_messages, client_media_entries
from features.conversation.replies import LinkBubble, MediaBubble
from features.conversation.reply_audio import cancel_reply_audio, client_reply_bubbles, synthesize_reply_audio
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.store_contracts import AssistantTurnData
from features.conversation.title_generation import auto_generate_title
from features.conversation.turn_access import turn_conversation
from features.conversation.turn_store import (
    persist_assistant_message,
    persist_tool_call,
    persist_tool_results,
    replace_assistant_message,
)
from infrastructure.ai.responses import message_to_response_items
from infrastructure.ai.tasks import AiTaskKey
from infrastructure.ai.usage import CacheUsageSummary
from infrastructure.async_io import settle_task
from infrastructure.constants import DEFAULT_SESSION_TITLE, TITLE_GENERATION_TEMPERATURE
from infrastructure.database import session_scope
from infrastructure.logger import get_logger
from infrastructure.text import resolve_language
from runtime.background import TaskBag
from runtime.memory_scope import MemoryScope
from runtime.tools.registry import REGISTRY

from workflows.chat.background_review import run_background_memory_review
from workflows.chat.chat_emitter import Emitter
from workflows.chat.streaming import _LLMTurnResult
from workflows.chat.tool_dispatch import (
    _BatchProgress,
    _run_tool_batch,
    _ToolDispatchContext,
    interrupted_tool_results,
    matched_tool_names,
)
from workflows.chat.turn_inputs import parse_temperature
from workflows.chat.types import TrackTask

logger = get_logger(__name__)


def _turn_data(result: _LLMTurnResult) -> AssistantTurnData:
    return AssistantTurnData(
        result.turn_content,
        result.tool_calls_list,
        result.reasoning,
        result.final_prompt_tokens,
        result.final_completion_tokens,
        result.turn_duration_ms,
        result.reply,
        result.generation_origin,
    )


# track_task=None 路径的兜底：模块级强引用集合，防止 CPython GC 在 await 期间销毁进行中的 task。
_BG = TaskBag("chat.persistence")
_PERSISTENCE_WRITE_TIMEOUT_SECONDS = 30.0


def _start_persistence_write(coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    """写入由回合持有；连接池等待、提交和关闭均纳入有限数据库预算。"""

    async def bounded_write() -> None:
        async with asyncio.timeout(_PERSISTENCE_WRITE_TIMEOUT_SECONDS):
            await coro

    return asyncio.create_task(bounded_write(), name=name)


async def _wait_persistence_write(task: asyncio.Task[None]) -> None:
    """取消只终止回合，先等已交接写入真实收尾，避免解锁后仍有迟到写入。"""
    owner = asyncio.current_task()
    cancelled = owner is not None and owner.cancelling() > 0
    try:
        await settle_task(task)
    except Exception as exc:
        if cancelled or (owner is not None and owner.cancelling() > 0):
            logger.warning("Interrupted turn persistence failed", extra={"task": task.get_name()}, exc_info=exc)
            raise asyncio.CancelledError from exc
        raise
    if cancelled or (owner is not None and owner.cancelling() > 0):
        raise asyncio.CancelledError


def _on_bg_error(task: asyncio.Task) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.warning("background task raised after completion", exc_info=exc)


def _spawn_post_turn_task(
    user_id: int,
    coro: Coroutine[Any, Any, object],
    track_task: TrackTask | None,
    *,
    session_id: str,
    transcripts: dict[str, str | Exception],
) -> None:
    """回合后任务交给调用方跟踪；无跟踪方时纳入模块级强引用集合并登记到用户任务。失败统一记录日志。"""

    async def run() -> None:
        try:
            await ensure_audio_transcripts(user_id, session_id, transcripts=transcripts)
            await coro
        finally:
            coro.close()

    task = asyncio.create_task(run())
    if track_task:
        track_task(task)
        task.add_done_callback(_on_bg_error)
    else:
        _BG.add(task, user_id=user_id, on_error=_on_bg_error)


async def _persist_tool_call_row(conv_id: int, result: _LLMTurnResult) -> None:
    await persist_tool_call(conv_id, _turn_data(result))


async def _persist_interrupted_tool_results(
    call_row: asyncio.Task[None],
    conv_id: int,
    results: list[tuple[str, Any]],
) -> None:
    """等调用行提交结束再写中断结果：结果行 id 须排在调用行之后，调用行未落库时不写孤立结果。"""
    try:
        await call_row
    except Exception:
        logger.warning(
            "Tool call row was not saved; interrupted tool results skipped",
            extra={"conversation_id": conv_id},
            exc_info=True,
        )
        return
    await persist_tool_results(conv_id, results)


async def _persist_assistant_no_tool_turn(
    conv: ConversationSnapshot,
    user_id: int,
    result: _LLMTurnResult,
    *,
    task: AiTaskKey,
    emitter: Emitter,
    effective_settings: dict[str, Any],
    user_text: str,
    first_user_msg_content: str | None,
    memory_scope: MemoryScope | None,
    media: list[dict[str, str]] | None,
    turn_reasoning: str | None,
    cache_usage: CacheUsageSummary,
    persist: bool,
    track_task: TrackTask | None,
    replace_reply_message_id: int | None = None,
    submission_request_id: str | None = None,
) -> None:
    """保存终端答复与媒体，交付气泡，并调度回合后任务。陪伴用户会话使用结构化回复，与文本回复的媒体附件互斥。"""
    reply = result.reply
    turn_content = result.turn_content
    if replace_reply_message_id is not None:
        message_id = await replace_assistant_message(
            conv.id,
            user_id,
            replace_reply_message_id,
            _turn_data(result),
            media,
            request_id=submission_request_id,
        )
        await cancel_reply_audio(user_id, {replace_reply_message_id})
        async with session_scope() as db:
            messages = await build_session_messages(conv.id, db, only_ids=[message_id])
            current = await turn_conversation(db, str(conv.id), user_id)
        if current is None:
            raise LookupError("Replacement conversation is no longer available")
        await emitter.send_json(
            {
                "type": "message.replaced",
                "session_id": str(conv.id),
                "replaced_message_id": replace_reply_message_id,
                "message": messages[0],
                "history_revision": current.history_revision,
            },
        )
        if result.reply is not None:
            try:
                await synthesize_reply_audio(user_id, message_id)
            except LookupError:
                raise
            except Exception:
                logger.warning("Replacement reply audio unavailable", extra={"message_id": message_id}, exc_info=True)
        return
    assistant_message_id: int | None = None
    if persist and (turn_content or media or result.reasoning):
        assistant_message_id = await persist_assistant_message(conv.id, user_id, _turn_data(result), media)
    if reply and assistant_message_id is not None:
        message_id = assistant_message_id

        async def deliver_bubble(index: int, bubble: dict[str, Any]) -> None:
            await emitter.send_json(
                {
                    "type": "bubble.append",
                    "message_id": message_id,
                    "bubble_index": index,
                    "bubble": bubble,
                },
            )

        try:
            # 逐泡交付：语音泡音频提交后、其余按序就绪即交付，保持气泡原顺序。
            reply = await synthesize_reply_audio(user_id, message_id, on_bubble_ready=deliver_bubble)
        except LookupError:
            # 消息已被修改或删除：不再交付后续气泡与完成帧，已逐泡交付的气泡保留（同取消路径），错误卡随后追加。
            raise
        except Exception:
            logger.warning(
                "Reply audio unavailable; delivering saved dialogue",
                extra={"message_id": message_id},
                exc_info=True,
            )
    reply_bubbles = (
        [
            json.dumps(bubble.model_dump(include={"type", "media_id", "status"}), ensure_ascii=False)
            if isinstance(bubble, MediaBubble)
            else json.dumps(bubble.model_dump(), ensure_ascii=False)
            if isinstance(bubble, LinkBubble)
            else bubble.text
            for bubble in reply.bubbles
        ]
        if reply is not None
        else None
    )
    audio_transcripts: dict[str, str | Exception] = {}
    # 首条用户消息只有附件时文字为空串，转写按需准备后仍可生成标题。
    if persist and conv.title == DEFAULT_SESSION_TITLE and first_user_msg_content is not None and turn_content:
        _spawn_post_turn_task(
            user_id,
            auto_generate_title(
                conv.id,
                first_user_msg_content,
                reply_bubbles if reply_bubbles is not None else turn_content,
                user_id,
                task=task,
                language=resolve_language(effective_settings.get("language")),
                temperature=parse_temperature(
                    effective_settings.get("chat.title_generation_temperature"),
                    TITLE_GENERATION_TEMPERATURE,
                ),
            ),
            track_task,
            session_id=str(conv.id),
            transcripts=audio_transcripts,
        )

    # 自动化会话没有记忆域；设置 UI 写入 ``agent.enable_background_review``。
    bg_review = effective_settings.get("agent.enable_background_review", True) is True
    if memory_scope is not None and assistant_message_id is not None and bg_review:
        _spawn_post_turn_task(
            user_id,
            run_background_memory_review(
                memory_scope,
                session_id=conv.id,
                through_message_id=assistant_message_id,
            ),
            track_task,
            session_id=str(conv.id),
            transcripts=audio_transcripts,
        )

    await emitter.send_json(
        {
            "type": "message.complete",
            **({"text": turn_content} if reply is None else {}),
            **({"bubbles": client_reply_bubbles(reply)} if reply else {}),
            **({"reply": reply.model_dump(mode="json"), "content": turn_content} if reply and not persist else {}),
            **({"reasoning": turn_reasoning} if turn_reasoning else {}),
            **({"media": client_media_entries(media)} if media else {}),
            **({"usage": result.final_usage_payload} if result.final_usage_payload else {}),
            "cache_usage": asdict(cache_usage),
            **({"message_id": assistant_message_id} if assistant_message_id is not None else {}),
            "can_replace_reply": persist
            and conv.parent_id is None
            and not conv.is_automation
            and bool(turn_content or media),
        },
    )

    # 心情随已落库的陪伴回复更新；主动回合不落库。
    if persist and reply_bubbles is not None:
        _spawn_post_turn_task(
            user_id,
            update_mood_from_companion_turn(user_id, user_text, reply_bubbles),
            track_task,
            session_id=str(conv.id),
            transcripts=audio_transcripts,
        )


async def _persist_assistant_with_tool_calls_and_results(
    conv: ConversationSnapshot,
    result: _LLMTurnResult,
    dispatch_ctx: _ToolDispatchContext,
    context: dict[str, Any],
    active_tool_names: set[str],
    schemas_by_name: dict[str, dict],
    *,
    persist: bool,
) -> None:
    """持久化工具调用与结果、同步 Responses 输入并解锁 ``search_tools`` 命中的工具；媒体产物由回合状态统一管理。"""
    tool_calls_list = result.tool_calls_list
    context["input"].extend(tool_calls_list)
    dispatch_ctx.media_turn.tool_round += 1
    progress = _BatchProgress()
    # 调用行提交不随取消中断：取消落在提交或关闭会话的窗口时，仍等它落库后补记结果。
    call_row = (
        _start_persistence_write(_persist_tool_call_row(conv.id, result), "chat.persist-tool-call") if persist else None
    )
    try:
        if call_row is not None:
            await _wait_persistence_write(call_row)
        # 工具批处理必须在 DB 事务外执行，避免 runner / LLM 调用期间持有连接。
        tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx, progress)
    except asyncio.CancelledError:
        # 每个 tool_call 都要有对应结果行，否则下一轮上下文会出现孤立 tool_calls；已完成的照实保存，否则用户说“继续”时模型会重做已生效的副作用。
        if call_row is not None:
            await _wait_persistence_write(
                _start_persistence_write(
                    _persist_interrupted_tool_results(
                        call_row,
                        conv.id,
                        interrupted_tool_results(tool_calls_list, progress),
                    ),
                    "chat.persist-interrupted-tools",
                ),
            )
        raise

    for res in tool_results:
        context["input"].extend(message_to_response_items(res))
        if res.get("name") != "search_tools":
            continue
        for name in matched_tool_names(res.get("content", "")):
            active_tool_names.add(name)
            if name not in schemas_by_name and name not in dispatch_ctx.excluded_tool_names:
                schema = REGISTRY.get_schema(dispatch_ctx.user_id, name)
                if schema is not None:
                    schemas_by_name[name] = schema
    if persist:
        # 工具已经执行，结果保存同样不随取消中断。
        await _wait_persistence_write(
            _start_persistence_write(
                persist_tool_results(conv.id, [(res["tool_call_id"], res.get("content", "")) for res in tool_results]),
                "chat.persist-tool-results",
            ),
        )
