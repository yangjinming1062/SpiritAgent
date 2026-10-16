from copy import deepcopy
from dataclasses import asdict
from typing import Any

from features.configuration.settings.values import load_user_settings
from features.conversation.ai_routing import conversation_ai_task
from features.conversation.fork import SourceNotFoundError
from features.conversation.gateway_queries import (
    has_message,
    history_snapshot,
    last_submission,
    message_count,
    undo_history,
)
from features.conversation.slash_commands import (
    SlashCommandContext,
    resolve_slash_command,
    suggest_commands,
)
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.undo import UndoNotAllowedError
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.constants import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_SLASH_CONFIRM_REQUIRED,
    JSONRPC_SLASH_GENERIC,
)
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from runtime.conversation import (
    LastSubmissionSnapshot,
    RuntimeSession,
    SessionResumeResult,
    SessionRuntimeInfo,
    build_runtime_info,
    new_runtime_session,
)
from runtime.desktop.connection import UserEventPublisher
from runtime.desktop.jsonrpc import JsonRpcError
from runtime.gateway_contracts import GatewaySession, RegisterMethod

from workflows.chat.gateway_inputs import (
    _reject_read_only_session,
    _require_owned_conv,
    desktop_history_lock,
)
from workflows.chat.history_commands import _do_compress_history
from workflows.chat.submission_gateway import register_submission_handler
from workflows.chat.turn_inputs import merge_session_settings, resolve_inference_settings

logger = get_logger(__name__)


def register_chat_handlers(session: GatewaySession, register: RegisterMethod) -> None:
    user_id = session.user_id
    dispatcher = session.dispatcher
    runtime_sessions = session.runtime_sessions
    replay_buffer = dispatcher.replay_buffer
    publisher = UserEventPublisher(user_id)

    def _mount_runtime(conv: ConversationSnapshot) -> RuntimeSession:
        """历史同步复用已有 runtime，不能取消其他视图正在消费的回合。"""
        existing = runtime_sessions.get(str(conv.id))
        if existing is not None:
            return existing
        runtime = new_runtime_session(
            conversation_id=conv.id,
            settings_json=conv.settings_json,
            kind=conv.kind,
            is_automation=conv.is_automation,
        )
        runtime_sessions[runtime.session_id] = runtime
        return runtime

    def _require_runtime(params: dict[str, Any]) -> RuntimeSession:
        session_id = params["session_id"]
        runtime = runtime_sessions.get(session_id)
        if runtime is None:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, f"session not found: {session_id!r}")
        return runtime

    async def _runtime_info(runtime: RuntimeSession, conv: ConversationSnapshot) -> SessionRuntimeInfo:
        async with SESSION_LOCAL() as db:
            user_settings = await load_user_settings(db, user_id)
            llm_config = await resolve_user_llm_config(db, user_id, conversation_ai_task(conv))
        effective = merge_session_settings(user_settings, runtime.settings, conv=conv)
        settings = runtime.settings | asdict(resolve_inference_settings(effective, conv=conv))
        return build_runtime_info(llm_config, runtime, settings, system_preset_id=conv.system_preset_id)

    async def _last_submission(runtime: RuntimeSession) -> LastSubmissionSnapshot | None:
        return await last_submission(user_id, runtime.conversation_id)

    async def _mounted_history(
        conv: ConversationSnapshot,
        *,
        after_id: int | None = None,
        history_revision: int | None = None,
    ) -> dict[str, Any]:
        """在发布锁内读取历史和快照，再确定当前连接的事件水位。"""
        runtime = _mount_runtime(conv)
        async with runtime.snapshot_lock:
            conv = await _require_owned_conv(user_id, str(conv.id))
            if history_revision != conv.history_revision and (
                history_revision is not None or conv.history_revision > 0
            ):
                after_id = None
            messages, truncated, next_cursor, message_count = await history_snapshot(conv.id, after_id)
            latest_conv = await _require_owned_conv(user_id, str(conv.id))
            if latest_conv.history_revision != conv.history_revision:
                # 替换提交可能先于事件发布；持有发布锁时该回合无法收尾或开始下一次替换。
                conv = latest_conv
                after_id = None
                messages, truncated, next_cursor, message_count = await history_snapshot(conv.id)
            last_submission = await _last_submission(runtime)
            info = await _runtime_info(runtime, conv)
            snapshot = deepcopy(runtime.active_turn)
            if snapshot is not None and snapshot.message_ids:
                last_input = max(snapshot.message_ids)
                # 终端回复先落库、后逐泡交付；挂载时以持久消息替代其瞬态副本。
                if any(
                    message["role"] == "assistant" and message["id"] > last_input and not message.get("tool_calls")
                    for message in messages
                ):
                    snapshot.text = ""
                    snapshot.reasoning = ""
                    snapshot.bubbles = []
            await dispatcher.flush_unsent()
            return SessionResumeResult(
                session_id=runtime.session_id,
                message_count=message_count,
                messages=messages,
                info=info,
                current_seq=replay_buffer.max_seq,
                stream_id=dispatcher.stream_id,
                active_turn=snapshot,
                last_submission=last_submission,
                truncated=truncated,
                next_cursor=next_cursor,
                incremental=after_id is not None,
                history_revision=conv.history_revision,
            ).model_dump()

    async def publish_history_change(event_type: str, payload: object, session_id: str) -> None:
        runtime = runtime_sessions.get(session_id)
        if runtime is None:
            await publisher.push_event(event_type, payload, session_id=session_id)
            return
        async with runtime.snapshot_lock:
            await publisher.push_event(event_type, payload, session_id=session_id)

    async def session_resume(params: dict) -> dict:
        stored_id = params["session_id"]
        last_seq = params.get("last_seq")
        after_id = params.get("after_id")
        history_revision = params.get("history_revision")
        conv = await _require_owned_conv(user_id, stored_id)

        # 重放流身份匹配且仍覆盖游标时才重放，否则回源恢复。
        revision_matches = history_revision == conv.history_revision or (
            history_revision is None and conv.history_revision == 0
        )
        if (
            revision_matches
            and last_seq is not None
            and last_seq > 0
            and params.get("stream_id") == dispatcher.stream_id
        ):
            runtime = _mount_runtime(conv)
            async with runtime.snapshot_lock:
                replayed_count = await dispatcher.replay(last_seq)
                if replayed_count is not None:
                    total_count = await message_count(conv.id)
                    logger.info(
                        "session.resume replayed frames",
                        extra={
                            "user_id": user_id,
                            "session_id": runtime.session_id,
                            "replayed": replayed_count,
                            "last_seq": last_seq,
                        },
                    )
                    return SessionResumeResult(
                        session_id=runtime.session_id,
                        message_count=total_count,
                        info=await _runtime_info(runtime, conv),
                        resumed=True,
                        replayed_count=replayed_count,
                        current_seq=replay_buffer.max_seq,
                        stream_id=dispatcher.stream_id,
                        last_submission=await _last_submission(runtime),
                        active_turn=deepcopy(runtime.active_turn),
                        history_revision=conv.history_revision,
                    ).model_dump()

        # 本地已有历史：锚点仍存在时只回增量，避免冷启动全量重拉。
        if after_id:
            anchor_exists = await has_message(conv.id, after_id)
            if anchor_exists:
                logger.info(
                    "session.resume incremental",
                    extra={
                        "user_id": user_id,
                        "session_id": stored_id,
                        "after_id": after_id,
                    },
                )
                return await _mounted_history(conv, after_id=after_id, history_revision=history_revision)

        # 客户端序列号失同步或超时，回退到 DB 历史防御性截断重水化
        logger.info("session.resume full reload", extra={"user_id": user_id, "session_id": stored_id})
        return await _mounted_history(conv)

    register("session.resume", session_resume)

    async def session_interrupt(params: dict) -> dict:
        runtime = _require_runtime(params)
        if (
            runtime.chat_task is not None
            and not runtime.chat_task.done()
            and not runtime.chat_task.cancelling()
            and (runtime.active_turn is None or runtime.active_turn.running)
        ):
            runtime.cancel_requested = True
            runtime.chat_task.cancel()
        return {}

    register("session.interrupt", session_interrupt)

    async def session_compress_context(params: dict) -> dict:
        runtime = _require_runtime(params)
        _reject_read_only_session(runtime)
        async with desktop_history_lock(runtime.session_id):
            if runtime.busy:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")
            conv = await _require_owned_conv(user_id, runtime.session_id)
            result = await _do_compress_history(conv, user_id, runtime, session.desktop_client_context)
            if result["compressed"]:
                await publish_history_change(
                    "command.result",
                    {
                        "command": "compress",
                        "result": {
                            "status": "ok",
                            "message": f"已压缩 {result['replaced_count']} 条早期消息",
                            "hydrate": True,
                            "payload": result,
                        },
                    },
                    session_id=runtime.session_id,
                )
            return result

    register("session.compress_context", session_compress_context)

    async def session_undo_to_message(params: dict) -> dict:
        session_id = params["session_id"]
        if not bool(params.get("confirmed")):
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                "session.undo_to_message requires confirmed=true",
                details={"requires_confirmation": True},
            )
        source_message_id = params["source_message_id"]
        owned = await _require_owned_conv(user_id, session_id)
        session_id = str(owned.id)
        runtime = runtime_sessions.get(session_id)
        async with desktop_history_lock(session_id):
            if runtime is not None and runtime.busy:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, "当前会话有正在生成的回复，请稍后再试")
            try:
                result = await undo_history(user_id, session_id, source_message_id)
            except (UndoNotAllowedError, SourceNotFoundError) as exc:
                raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        await publish_history_change(
            "message.deleted",
            {
                "session_id": session_id,
                "deleted_count": result["deleted_count"],
                "anchor": result["anchor"],
                "messages": result["messages"],
            },
            session_id=session_id,
        )
        logger.info(
            "session.undo_to_message",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "source_message_id": source_message_id,
                "deleted_count": result["deleted_count"],
            },
        )
        return result

    register("session.undo_to_message", session_undo_to_message)

    async def command_dispatch(params: dict) -> dict:
        """Slash 命令分发：按 command 查 SLASH_COMMANDS 并执行 handler，返回 {command, result} 并同步广播 command.result 事件给同 session 各窗口。"""
        runtime = _require_runtime(params)
        raw_command = params["command"]
        if not raw_command.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command must be a non-empty string")

        name = raw_command.strip().lstrip("/").lower()
        if not name:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command name is empty")

        cmd = resolve_slash_command(name)
        if cmd is None:
            suggestions = suggest_commands(name)
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"unknown command: /{name}",
                details={"suggestions": suggestions},
            )

        confirmed = bool(params.get("confirmed"))
        if cmd.requires_confirmation and not confirmed:
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                f"command /{name} requires explicit confirmation",
                details={"requires_confirmation": True, "command": cmd.name},
            )

        args_raw = params.get("args")
        args_list: list[str] = []
        if isinstance(args_raw, list):
            args_list = [str(arg) for arg in args_raw]
        elif isinstance(args_raw, str):
            args_list = args_raw.split()

        ctx = SlashCommandContext(
            session_id=runtime.session_id,
            user_id=user_id,
            runtime=runtime,
            args=args_list,
            client_context=session.desktop_client_context,
        )

        try:
            result = await cmd.handler(ctx)
        except JsonRpcError:
            raise
        except Exception:
            logger.exception("slash command handler failed", extra={"command": cmd.name, "user_id": user_id})
            raise JsonRpcError(JSONRPC_SLASH_GENERIC, f"command /{name} failed unexpectedly") from None

        result_payload = {"command": cmd.name, "result": asdict(result)}
        await publish_history_change("command.result", result_payload, session_id=runtime.session_id)
        return result_payload

    register("command.dispatch", command_dispatch)

    register_submission_handler(session, register, _require_runtime, publish_history_change)
