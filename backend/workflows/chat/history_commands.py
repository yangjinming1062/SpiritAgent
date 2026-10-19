import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from features.conversation.memory_scope import conversation_memory_scope
from features.conversation.session_operations import ConversationNotFoundError, all_messages, clear_history
from features.conversation.slash_commands import SlashCommandContext, SlashCommandResult, register_slash_command
from features.conversation.snapshots import ConversationSnapshot
from features.memory.memory_namespaces import normalize_recall_context
from features.memory.use_cases import remember_manual
from infrastructure.ai.llm_client import MissingLlmConfigError
from infrastructure.config import SETTINGS
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from runtime.client_context import ChatRequestClientContext
from runtime.conversation import RuntimeSession

from workflows.chat.context_compressor import CompressionFailedError
from workflows.chat.inputs import _reject_read_only_session, _require_owned_conv, conversation_history_lock
from workflows.chat.orchestrator import compress_session_history

logger = get_logger(__name__)


@asynccontextmanager
async def compression_context(
    user_id: int,
    runtime: RuntimeSession,
    *,
    busy_message: str = "当前会话有正在生成的回复，请稍后再试",
) -> AsyncIterator[ConversationSnapshot]:
    _reject_read_only_session(runtime)
    async with conversation_history_lock(runtime.session_id):
        if runtime.busy:
            raise RequestError(409, busy_message, code="turn_busy")
        yield await _require_owned_conv(user_id, runtime.session_id)


async def _do_compress_history(
    conv: ConversationSnapshot,
    user_id: int,
    runtime: RuntimeSession,
    client_context: ChatRequestClientContext | None,
) -> dict[str, Any]:
    """历史压缩与 /压缩 命令的共用实现；调用方持会话锁并已校验在途回合。compressed=False 时返回体不含 messages/summary，True 时含 delivered messages 给前端 hydrate；摘要失败抛业务错误，不当作无需压缩。"""
    try:
        result = await compress_session_history(conv, user_id, runtime.settings, client_context)
    except MissingLlmConfigError as exc:
        raise RequestError(500, "模型服务暂未配置，请联系管理员") from exc
    except CompressionFailedError as exc:
        raise RequestError(500, "上下文压缩失败，请稍后重试") from exc
    usage = {"total_tokens": result.total_tokens, "context_window": result.context_window}
    if result.info is None:
        return {
            "session_id": runtime.session_id,
            "compressed": False,
            "reason": "历史消息较少，无需压缩（至少需要保留最近对话）",
            "usage": usage,
        }
    delivered = await all_messages(conv.id)
    return {
        "session_id": runtime.session_id,
        "compressed": True,
        "replaced_count": result.info.replaced_count,
        "summary": result.info.summary,
        "messages": delivered,
        "usage": usage,
    }


def compression_command_result(result: dict[str, Any]) -> SlashCommandResult:
    compressed = result["compressed"]
    return SlashCommandResult(
        status="ok",
        message=f"已压缩 {result['replaced_count']} 条早期消息" if compressed else "历史消息较少，无需压缩",
        hydrate=compressed,
        payload=result,
    )


@register_slash_command(
    name="clear",
    aliases=("清空", "清理", "reset"),
    description="清空当前会话消息（保留会话行）",
    requires_confirmation=True,
)
async def _slash_clear(ctx: SlashCommandContext) -> SlashCommandResult:
    _reject_read_only_session(ctx.runtime)
    async with conversation_history_lock(ctx.session_id):
        if ctx.runtime.busy:
            raise RequestError(409, "请先停止当前生成再清理会话", code="turn_busy")
        try:
            result = await clear_history(ctx.user_id, ctx.session_id)
        except ConversationNotFoundError as exc:
            raise RequestError(404, str(exc)) from exc
    cleared = result["cleared_count"]
    return SlashCommandResult(
        status="ok",
        message="当前会话无消息可清空" if cleared == 0 else f"已清空 {cleared} 条消息",
        hydrate=True,
        payload=result,
    )


@register_slash_command(
    name="compress",
    aliases=("压缩", "ctx"),
    description="压缩当前会话上下文",
    requires_confirmation=False,
)
async def _slash_compress(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/压缩`` 命令 handler：复用历史压缩用例。"""
    async with compression_context(
        ctx.user_id,
        ctx.runtime,
        busy_message="请先停止当前生成再压缩会话",
    ) as conv:
        try:
            result = await _do_compress_history(conv, ctx.user_id, ctx.runtime, ctx.client_context)
        except RequestError as exc:
            return SlashCommandResult(status="error", message=exc.message, hydrate=False)
    return compression_command_result(result)


@register_slash_command(
    name="remember",
    aliases=("记住", "记忆", "remind", "memo", "memory"),
    description="主动记住指定内容到长期记忆",
    requires_confirmation=False,
)
async def _slash_remember(ctx: SlashCommandContext) -> SlashCommandResult:
    """``/记住`` 命令 handler：主动将指定内容持久化写入长期记忆并触发向量化。"""
    content = " ".join(ctx.args).strip()
    if not content:
        return SlashCommandResult(
            status="ok",
            message="请提供要记住的内容，例如：/记住 我喜欢喝无糖乌龙茶",
            hydrate=False,
        )

    max_chars = SETTINGS.memory_recall_max_content_chars
    if "\x00" in content:
        return SlashCommandResult(status="error", message="要记住的内容不能包含空字符")
    if len(content) > max_chars:
        return SlashCommandResult(status="error", message=f"要记住的内容最多 {max_chars} 个字符，请精简后再试")
    context = normalize_recall_context("manual")
    tags = json.dumps(["user_preference"])
    importance = 1.5

    conv = await _require_owned_conv(ctx.user_id, ctx.session_id)
    scope = conversation_memory_scope(conv, ctx.user_id)
    if scope is None:
        return SlashCommandResult(status="error", message="定时任务会话不保存长期记忆，无法记住内容")
    mem = await remember_manual(scope, int(ctx.session_id), content, context, tags, importance)

    display_content = content if len(content) <= 60 else f"{content[:57]}..."
    return SlashCommandResult(
        status="ok",
        message=f"已记住：{display_content}",
        hydrate=False,
        payload={"memory_id": mem.id, "content": content},
    )
