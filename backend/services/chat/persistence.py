import asyncio
import json
from typing import Any

from components import (
    ATTACHMENT_TYPE_VIDEO,
    BACKGROUND_REVIEW_DEFAULT,
    DEFAULT_LANGUAGE,
    TITLE_GENERATION_TEMPERATURE,
    TaskBag,
    get_logger,
    safe_json_loads,
    session_scope,
)
from modules.conversation import Conversation, Message
from modules.system import ChatRequest
from sqlalchemy.ext.asyncio import AsyncSession

from services.companion import emit_companion_affect

from ..conversation import AFFECT_TRACE_SUBTYPE
from ..llm import copy_responses_context, message_to_response_items
from ..scheduler import auto_generate_title, run_background_memory_review
from ..tools import REGISTRY
from .chat_emitter import Emitter
from .tool_dispatch import _run_tool_batch, _ToolDispatchContext
from .turn_inputs import parse_temperature
from .types import TrackTask

logger = get_logger(__name__)

# track_task=None 路径的兜底：模块级强引用集合，防止 CPython GC 在 await 期间销毁进行中的 task。
# 与 scheduler/cron.py 的 _BG 同模式（TaskBag 在 components/background.py）。
_BG = TaskBag("chat.persistence")


def _on_bg_error(task: asyncio.Task) -> None:
    if (exc := task.exception()) is not None:
        logger.warning("background task raised after completion", exc_info=exc)


def _track_background_task(task: asyncio.Task) -> None:
    """将 task 纳入模块级强引用集合，done 时自动移除并打日志。"""
    _BG.add(task, on_error=_on_bg_error)


def _coerce_tool_result_content(content: Any) -> str:
    """Message.content 是 Text 列，非字符串负载 JSON 编码后提交，避免类型错误。"""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


_MEDIA_TOOL_NAMES = frozenset({"image_generate", "video_generate"})


def extract_turn_media(tool_results: list[dict]) -> list[dict[str, str]]:
    """从工具结果中提取可送达渲染端的生成媒体；pending 与失败结果跳过。"""
    media: list[dict[str, str]] = []
    for res in tool_results:
        if res.get("name") not in _MEDIA_TOOL_NAMES:
            continue
        parsed = safe_json_loads(res.get("content", ""), default=None)
        if not isinstance(parsed, dict) or not parsed.get("success"):
            continue
        if res.get("name") == "image_generate":
            urls = parsed.get("urls")
            if isinstance(urls, list):
                media.extend({"type": "image", "url": u} for u in urls if isinstance(u, str) and u)
        elif isinstance(parsed.get("url"), str) and parsed["url"]:
            media.append({"type": "video", "url": parsed["url"]})
    return media


def _build_persisted_content_from_parts(text: str, attachments: list[dict] | None) -> tuple[str, str]:
    if not attachments:
        return text or "", "text"
    parts = [{"type": "input_text", "text": text or ""}]
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
    return _coerce_tool_result_content(parts), "multimodal_v1"


def _build_persisted_content(req: "ChatRequest") -> tuple[str, str]:
    """把 req.message + 附件转换为 ``(content, content_type)``：纯文本返回 ``(str, "text")``；多模态返回带 ``multimodal_v1`` 标签的 JSON parts 数组，附件以扁平 URL 的 ``input_image``/``input_video`` 发出（与 Responses API 形状一致）。"""
    text = req.message.content or ""
    attachments = getattr(req.message, "attachments", None) or []
    return _build_persisted_content_from_parts(text, attachments)


async def persist_extra_user_messages(db: AsyncSession, conv_id: int, items: list[dict]) -> list[int]:
    """在运行最终消息轮次前，批量持久化前置 user 消息，返回按插入序的行 id。"""
    rows: list[Message] = []
    for item in items:
        text = item.get("text") or ""
        attachments = item.get("attachments") or []
        db_content, db_content_type = _build_persisted_content_from_parts(text, attachments)
        row = Message(conversation_id=conv_id, role="user", content=db_content, content_type=db_content_type)
        db.add(row)
        rows.append(row)
    await db.commit()
    return [row.id for row in rows if isinstance(row.id, int)]


async def _persist_user_message(db: AsyncSession, conv: Conversation, req: ChatRequest) -> int:
    """插入 user 角色 Message 行并提交，返回行 id。"""
    db_content, db_content_type = _build_persisted_content(req)
    row = Message(conversation_id=conv.id, role=req.message.role, content=db_content, content_type=db_content_type, tool_call_id=req.message.tool_call_id)
    db.add(row)
    await db.commit()
    return row.id


def _affect_trace_content(emotion: str | None, actions: list[str]) -> str:
    """纯肢体语言回复（无文本）的结构化标记：以 assistant 行持久化，确保下一轮 LLM 上下文仍能看到伙伴已做出反应。"""
    parts: list[str] = []
    if emotion and emotion != "neutral":
        parts.append(f"[affect:{emotion}]")
    for action in actions:
        parts.append(f"[action:{action}]")
    return "\n".join(parts)


async def _persist_assistant_no_tool_turn(
    conv: Conversation,
    user_id: int,
    effective_settings: dict,
    emitter: Emitter,
    req: ChatRequest,
    turn_content: str,
    final_prompt_tokens: int,
    final_completion_tokens: int,
    final_usage_payload: dict | None,
    turn_duration_ms: int,
    llm_config: dict,
    first_user_msg_content: str | None,
    context: dict[str, Any],
    track_task: TrackTask | None = None,
    *,
    provider_name: str = "",
    emotion: str | None = None,
    actions: list[str] | None = None,
    spatial_locale: str | None = None,
    spatial_target: str | None = None,
    mood: str | None = None,
    media: list[dict[str, str]] | None = None,
    reasoning: str | None = None,
    turn_reasoning: str | None = None,
) -> None:
    """终端路径：助手只产出文本（可附生成媒体）；持久化 Message、触发可选的标题生成与后台 review、发出 ``message.complete``。"""
    assistant_message_id: int | None = None
    if turn_content or media or reasoning:
        async with session_scope() as db:
            row = Message(
                conversation_id=conv.id,
                role="assistant",
                content=turn_content or None,
                media_json=json.dumps(media, ensure_ascii=False) if media else None,
                reasoning_content=reasoning or None,
                prompt_tokens=final_prompt_tokens,
                completion_tokens=final_completion_tokens,
                turn_duration_ms=turn_duration_ms,
            )
            db.add(row)
            await db.commit()
            assistant_message_id = row.id
    elif (emotion and emotion != "neutral") or actions:
        # 仅情绪反应：无文本，持久化轻量 assistant 行作为下一轮 LLM 上下文的反应痕迹，避免嘟嘴/动作在历史中消失。
        async with session_scope() as db:
            row = Message(
                conversation_id=conv.id,
                role="assistant",
                content=_affect_trace_content(emotion, actions or []),
                subtype=AFFECT_TRACE_SUBTYPE,
                prompt_tokens=final_prompt_tokens,
                completion_tokens=final_completion_tokens,
                turn_duration_ms=turn_duration_ms,
            )
            db.add(row)
            await db.commit()
            assistant_message_id = row.id

    if conv.title == "New Conversation" and first_user_msg_content and turn_content:
        title_temp = parse_temperature(effective_settings.get("chat.title_generation_temperature"), TITLE_GENERATION_TEMPERATURE)
        title_task = asyncio.create_task(
            auto_generate_title(
                conv.id,
                first_user_msg_content,
                turn_content,
                llm_config,
                language=effective_settings.get("language", DEFAULT_LANGUAGE),
                temperature=title_temp,
                provider_name=provider_name or None,
            ),
        )
        if track_task:
            track_task(title_task)
        else:
            _track_background_task(title_task)

    # 优先读命名空间键（设置 UI 写入 ``agent.enable_background_review``），旧数据回退到裸键 ``enable_background_review``。
    bg_review = effective_settings.get("agent.enable_background_review") or effective_settings.get("enable_background_review") or BACKGROUND_REVIEW_DEFAULT
    if bg_review.lower() == BACKGROUND_REVIEW_DEFAULT:
        review_task = asyncio.create_task(run_background_memory_review(user_id, llm_config, copy_responses_context(context)))
        if track_task:
            track_task(review_task)
        else:
            _track_background_task(review_task)

    if mood:
        await emit_companion_affect(user_id, mood=mood)

    affect_payload: dict[str, Any] = {"emotion": emotion}
    if actions:
        affect_payload["actions"] = actions
    if spatial_locale:
        affect_payload["locale"] = spatial_locale
    if spatial_target:
        affect_payload["target"] = spatial_target

    displayed_reasoning = turn_reasoning if turn_reasoning is not None else reasoning
    await emitter.send_json(
        {
            "type": "message.complete",
            "text": turn_content,
            **({"reasoning": displayed_reasoning} if displayed_reasoning else {}),
            "affect": affect_payload,
            **({"media": media} if media else {}),
            **({"usage": final_usage_payload} if final_usage_payload else {}),
            **({"message_id": assistant_message_id} if isinstance(assistant_message_id, int) else {}),
        },
    )


async def _persist_assistant_with_tool_calls_and_results(
    conv: Conversation,
    tool_calls_list: list[dict],
    turn_content: str,
    final_prompt_tokens: int,
    final_completion_tokens: int,
    turn_duration_ms: int,
    dispatch_ctx: _ToolDispatchContext,
    context: dict[str, Any],
    active_tool_names: set[str],
    schemas_by_name: dict[str, dict],
    *,
    reasoning: str | None = None,
) -> list[dict[str, str]]:
    """持久化含 tool_calls 的 assistant Message、跑工具批处理，并同步更新 Responses 输入轨迹；返回本轮生成的可送达媒体。"""
    if turn_content:
        context["input"].append({"role": "assistant", "content": [{"type": "output_text", "text": turn_content}]})
    context["input"].extend(tool_calls_list)
    async with session_scope() as db:
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content=turn_content if turn_content else None,
                tool_calls=json.dumps(tool_calls_list),
                reasoning_content=reasoning or None,
                prompt_tokens=final_prompt_tokens,
                completion_tokens=final_completion_tokens,
                turn_duration_ms=turn_duration_ms,
            ),
        )
        await db.commit()

    # 工具批处理必须在 DB 事务外执行，避免 runner / LLM 调用期间持有连接。
    try:
        tool_results = await _run_tool_batch(tool_calls_list, dispatch_ctx)
    except asyncio.CancelledError:
        # 为每个未完成的 tool_call 合成一条 tool 结果，避免 assistant 行出现孤立 tool_calls 导致下一轮 LLM 上下文畸形。
        cancelled_results = [
            {"role": "tool", "name": tc.get("name", ""), "tool_call_id": tc.get("call_id", ""), "content": json.dumps({"error": "cancelled"}, ensure_ascii=False)}
            for tc in tool_calls_list
        ]

        async def _persist_cancelled() -> None:
            async with session_scope() as cancel_db:
                for res in cancelled_results:
                    cancel_db.add(Message(conversation_id=conv.id, role="tool", tool_call_id=res["tool_call_id"], content=_coerce_tool_result_content(res.get("content", ""))))
                await cancel_db.commit()

        await asyncio.shield(_persist_cancelled())
        raise

    for res in tool_results:
        context["input"].extend(message_to_response_items(res))
        if res.get("name") == "search_tools":
            parsed = safe_json_loads(res.get("content", ""))
            if isinstance(parsed, dict):
                for t in parsed.get("matched_tools", []):
                    if not isinstance(t, dict) or not t.get("name"):
                        continue
                    name = t["name"]
                    active_tool_names.add(name)
                    if name not in schemas_by_name:
                        schema = REGISTRY.get_schema(dispatch_ctx.user_id, name)
                        if schema is not None:
                            schemas_by_name[name] = schema
    async with session_scope() as db:
        for res in tool_results:
            db.add(Message(conversation_id=conv.id, role="tool", tool_call_id=res["tool_call_id"], content=_coerce_tool_result_content(res.get("content", ""))))
        await db.commit()
    return extract_turn_media(tool_results)
