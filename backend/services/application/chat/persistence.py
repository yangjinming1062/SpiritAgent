import asyncio
import json
from collections.abc import Coroutine
from typing import Any

from components import (
    ATTACHMENT_TYPE_VIDEO,
    DEFAULT_SESSION_TITLE,
    TITLE_GENERATION_TEMPERATURE,
    TaskBag,
    get_logger,
    resolve_language,
    session_scope,
    track_user_task,
)
from modules.conversation import Conversation, MediaBubble, Message
from modules.system import ChatMessageRequest
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.contracts import MemoryScope
from services.domains.companion import update_mood_from_companion_turn
from services.domains.conversation import (
    bind_reply_videos,
    client_media_entries,
    client_reply_bubbles,
    synthesize_reply_audio,
)
from services.domains.media import prune_videos_in_range
from services.infrastructure.llm import UserLlmConfig, message_to_response_items
from services.infrastructure.tool_runtime import REGISTRY

from .background_review import run_background_memory_review
from .chat_emitter import Emitter
from .context_compressor import CompressionInfo
from .streaming import _LLMTurnResult
from .title_generator import auto_generate_title
from .tool_dispatch import _run_tool_batch, _ToolDispatchContext, matched_tool_names
from .turn_inputs import parse_temperature
from .types import TrackTask

logger = get_logger(__name__)

# track_task=None 路径的兜底：模块级强引用集合，防止 CPython GC 在 await 期间销毁进行中的 task。
_BG = TaskBag("chat.persistence")


def _on_bg_error(task: asyncio.Task) -> None:
    if (exc := task.exception()) is not None:
        logger.warning("background task raised after completion", exc_info=exc)


def _spawn_post_turn_task(user_id: int, coro: Coroutine[Any, Any, object], track_task: TrackTask | None) -> None:
    """回合后任务交给调用方跟踪；无跟踪方时纳入模块级强引用集合并登记到用户任务。"""
    task = asyncio.create_task(coro)
    if track_task:
        track_task(task)
    else:
        _BG.add(task, on_error=_on_bg_error)
        track_user_task(user_id, task)


def _coerce_tool_result_content(content: Any) -> str:
    """Message.content 是 Text 列，非字符串负载 JSON 编码后提交，避免类型错误。"""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _build_persisted_content(text: str, attachments: list[dict] | None) -> tuple[str, str]:
    """纯文本返回 ``(text, "text")``；有附件时返回 ``multimodal_v1`` 的 Responses 形状 parts 数组 JSON。"""
    if not attachments:
        return text, "text"
    parts = [{"type": "input_text", "text": text}]
    media_uris: list[str] = []
    for att in attachments:
        url = att.get("file_url")
        if not url:
            continue
        if att.get("type") == ATTACHMENT_TYPE_VIDEO:
            parts.append({"type": "input_video", "video_url": url})
        else:
            parts.append({"type": "input_image", "image_url": url})
        media_uris.append(url)
    if media_uris:
        logger.info("multimodal parts sent to LLM", extra={"media_count": len(media_uris), "media_uris": media_uris})
    return json.dumps(parts, ensure_ascii=False), "multimodal_v1"


async def persist_extra_user_messages(db: AsyncSession, conv_id: int, items: list[dict]) -> list[int]:
    """在运行最终消息轮次前，批量持久化前置 user 消息，返回按插入序的行 id。"""
    rows: list[Message] = []
    for item in items:
        db_content, db_content_type = _build_persisted_content(item.get("text") or "", item.get("attachments"))
        row = Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type)
        db.add(row)
        rows.append(row)
    await db.commit()
    return [row.id for row in rows]


async def persist_queued_inbound_message(
    db: AsyncSession,
    conv_id: int,
    *,
    text: str,
    attachments: list[dict] | None = None,
    dedup_key: str | None = None,
) -> Message | None:
    """IM 入站消息先持久化再确认接收：以 queued 标记落为会话行并返回。

    接收顺序即行 id 序；回合消费时整批清除标记。dedup_key 命中已有行（渠道重投）
    时返回 None，调用方不再入队。
    """
    db_content, db_content_type = _build_persisted_content(text, attachments)
    stmt = insert(Message).values(
        conversation_id=conv_id,
        role="user",
        content=db_content,
        content_type=db_content_type,
        queued=True,
        dedup_key=dedup_key,
    )
    row = (
        await db.execute(stmt.on_conflict_do_nothing(index_elements=[Message.dedup_key]).returning(Message))
    ).scalar_one_or_none()
    await db.commit()
    return row


async def _persist_user_message(db: AsyncSession, conv_id: int, message: ChatMessageRequest) -> int:
    """插入 user 角色 Message 行并提交，返回行 id。"""
    db_content, db_content_type = _build_persisted_content(message.content, message.attachments)
    row = Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type)
    db.add(row)
    await db.commit()
    return row.id


async def persist_compression_checkpoint(db: AsyncSession, conv_id: int, info: CompressionInfo) -> Message:
    """写入压缩检查点，下一轮历史从其覆盖边界之后读取；原消息保留，边界前无人引用的视频随之清理。"""
    checkpoint = Message(
        conversation_id=conv_id,
        role="system",
        content=info.checkpoint_text,
        subtype="compress_summary",
        summary_through_message_id=info.through_message_id,
        prompt_tokens=info.prompt_tokens,
        completion_tokens=info.completion_tokens,
    )
    db.add(checkpoint)
    await db.commit()
    if info.prune_before_message_id:
        await prune_videos_in_range(db, conv_id, hi=info.prune_before_message_id, preserve_queued=True)
        await db.commit()
    return checkpoint


async def _persist_tool_results(conv_id: int, results: list[tuple[str, Any]]) -> None:
    async with session_scope() as db:
        for call_id, content in results:
            db.add(
                Message(
                    conversation_id=conv_id,
                    role="tool",
                    tool_call_id=call_id,
                    content=_coerce_tool_result_content(content),
                ),
            )
        await db.commit()


async def _persist_assistant_no_tool_turn(
    conv: Conversation,
    user_id: int,
    result: _LLMTurnResult,
    *,
    emitter: Emitter,
    effective_settings: dict[str, Any],
    llm_config: UserLlmConfig,
    user_text: str,
    first_user_msg_content: str | None,
    memory_scope: MemoryScope | None,
    provider_name: str,
    media: list[dict[str, str]] | None,
    turn_reasoning: str | None,
    persist: bool,
    track_task: TrackTask | None,
) -> None:
    """保存终端答复与媒体，交付气泡，并调度回合后任务。

    结构化回复只出现在固定陪伴会话；文本渠道的媒体附件与结构化回复互斥。
    """
    reply = result.reply
    turn_content = result.turn_content
    assistant_message_id: int | None = None
    if persist and (turn_content or media or result.reasoning):
        async with session_scope() as db:
            row = Message(
                conversation_id=conv.id,
                role="assistant",
                content=turn_content or None,
                content_type="companion_reply" if reply is not None else "text",
                media_json=json.dumps(media, ensure_ascii=False) if media else None,
                reasoning_content=result.reasoning or None,
                reply_json=reply.model_dump_json() if reply else None,
                prompt_tokens=result.final_prompt_tokens,
                completion_tokens=result.final_completion_tokens,
                turn_duration_ms=result.turn_duration_ms,
            )
            db.add(row)
            if reply is not None:
                await db.flush()
                await bind_reply_videos(db, row, reply, user_id)
            await db.commit()
            assistant_message_id = row.id
    if reply and assistant_message_id is not None:
        reply = await synthesize_reply_audio(user_id, assistant_message_id)
    reply_bubbles = (
        [
            json.dumps(bubble.model_dump(include={"type", "media_id", "status"}), ensure_ascii=False)
            if isinstance(bubble, MediaBubble)
            else bubble.text
            for bubble in reply.bubbles
        ]
        if reply is not None
        else None
    )
    if persist and conv.title == DEFAULT_SESSION_TITLE and first_user_msg_content and turn_content:
        _spawn_post_turn_task(
            user_id,
            auto_generate_title(
                conv.id,
                first_user_msg_content,
                reply_bubbles if reply_bubbles is not None else turn_content,
                llm_config,
                language=resolve_language(effective_settings.get("language")),
                temperature=parse_temperature(
                    effective_settings.get("chat.title_generation_temperature"),
                    TITLE_GENERATION_TEMPERATURE,
                ),
                provider_name=provider_name or None,
            ),
            track_task,
        )

    # 自动化会话没有记忆域；设置 UI 写入 ``agent.enable_background_review``。
    bg_review = effective_settings.get("agent.enable_background_review", True) is True
    if memory_scope is not None and assistant_message_id is not None and bg_review:
        _spawn_post_turn_task(
            user_id,
            run_background_memory_review(
                memory_scope,
                llm_config,
                session_id=conv.id,
                through_message_id=assistant_message_id,
            ),
            track_task,
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
            **({"message_id": assistant_message_id} if assistant_message_id is not None else {}),
        },
    )

    # 心情只随已落库的桌面陪伴回复更新；主动回合不落库。
    if persist and reply_bubbles is not None:
        _spawn_post_turn_task(
            user_id,
            update_mood_from_companion_turn(user_id, user_text, reply_bubbles, llm_config),
            track_task,
        )


async def _persist_assistant_with_tool_calls_and_results(
    conv: Conversation,
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
    if persist:
        async with session_scope() as db:
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=None,
                    tool_calls=json.dumps(tool_calls_list),
                    reasoning_content=result.reasoning or None,
                    prompt_tokens=result.final_prompt_tokens,
                    completion_tokens=result.final_completion_tokens,
                    turn_duration_ms=result.turn_duration_ms,
                ),
            )
            await db.commit()

    # 工具批处理必须在 DB 事务外执行，避免 runner / LLM 调用期间持有连接。
    try:
        tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx)
    except asyncio.CancelledError:
        # 为每个未完成的 tool_call 合成一条 tool 结果，避免 assistant 行出现孤立 tool_calls 导致下一轮 LLM 上下文畸形。
        if persist:
            cancelled = json.dumps({"error": "cancelled"}, ensure_ascii=False)
            await asyncio.shield(
                _persist_tool_results(conv.id, [(tc.get("call_id", ""), cancelled) for tc in tool_calls_list]),
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
        await _persist_tool_results(conv.id, [(res["tool_call_id"], res.get("content", "")) for res in tool_results])
