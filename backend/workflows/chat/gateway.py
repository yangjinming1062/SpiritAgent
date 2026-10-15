from copy import deepcopy
from dataclasses import asdict
from typing import Any

from features.configuration.settings.values import load_user_settings
from features.conversation.ai_routing import conversation_ai_task
from features.conversation.fork import ForkNotAllowedError, SourceNotFoundError
from features.conversation.gateway_queries import (
    ConversationNotFoundError,
    ForkAttachmentError,
    create_conversation,
    ensure_main_conversation,
    fork_conversation,
    has_message,
    history_page,
    history_snapshot,
    last_submission,
    message_count,
    undo_history,
    update_session_settings,
)
from features.conversation.request_contracts import PromptPresetListResponse, PromptPresetSummary
from features.conversation.slash_commands import (
    SlashCommandContext,
    list_commands_for_user,
    resolve_slash_command,
    suggest_commands,
)
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.undo import UndoNotAllowedError
from infrastructure.ai.user_config import resolve_user_llm_config
from infrastructure.constants import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_SLASH_CONFIRM_REQUIRED,
    JSONRPC_SLASH_GENERIC,
)
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from pydantic import ValidationError
from runtime.conversation import (
    LastSubmissionSnapshot,
    RuntimeSession,
    SessionCreateResult,
    SessionResumeResult,
    SessionRuntimeInfo,
    SessionSettingsPatch,
    build_runtime_info,
    new_runtime_session,
)
from runtime.desktop.connection import UserEventPublisher
from runtime.desktop.jsonrpc import JsonRpcError
from runtime.gateway_contracts import GatewaySession, RegisterMethod
from runtime.presets import SYSTEM_PRESET_CATALOG

from workflows.chat.gateway_inputs import (
    _is_nonneg_int,
    _reject_read_only_session,
    _require_nonneg_int,
    _require_owned_conv,
    _require_str,
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
        session_id = _require_str(params, "session_id")
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

    async def session_get_main(_params: dict) -> dict:
        conv = await ensure_main_conversation(user_id)
        return await _mounted_history(conv)

    register("session.get_main", session_get_main)

    async def session_create(params: dict) -> dict:
        preset_id = params.get("system_preset_id")
        if not isinstance(preset_id, str) or preset_id not in SYSTEM_PRESET_CATALOG:
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"system_preset_id must be one of {sorted(SYSTEM_PRESET_CATALOG)}",
            )
        conv = await create_conversation(user_id, preset_id)
        runtime = _mount_runtime(conv)
        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
        logger.info(
            "session.create",
            extra={"user_id": user_id, "session_id": runtime.session_id, "system_preset_id": preset_id},
        )
        await dispatcher.flush_unsent()
        return SessionCreateResult(
            session_id=runtime.session_id,
            info=await _runtime_info(runtime, conv),
        ).model_dump()

    register("session.create", session_create)

    async def system_list_presets(_params: dict) -> dict:
        """返回内置系统预设的元数据清单（不含 body）。body 永远不下发到客户端。"""
        return PromptPresetListResponse(
            presets=[
                PromptPresetSummary(id=p.id, name=p.name, description=p.description, icon_key=p.icon_key)
                for p in SYSTEM_PRESET_CATALOG.values()
            ],
        ).model_dump()

    register("system.list_presets", system_list_presets)

    async def session_fork(params: dict) -> dict:
        source_session_id = _require_str(params, "source_session_id")
        source_message_id = _require_nonneg_int(params, "source_message_id")
        try:
            conv, message_count = await fork_conversation(user_id, source_session_id, source_message_id)
        except ForkNotAllowedError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc
        except (SourceNotFoundError, ConversationNotFoundError) as exc:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, str(exc)) from exc
        except ForkAttachmentError as exc:
            raise JsonRpcError(JSONRPC_INTERNAL_ERROR, str(exc)) from exc
        logger.info(
            "session.fork",
            extra={
                "user_id": user_id,
                "source_session_id": source_session_id,
                "source_message_id": source_message_id,
                "new_session_id": str(conv.id),
                "message_count": message_count,
            },
        )
        await publisher.push_event("session.list_changed", {"session_id": str(conv.id)})
        return await _mounted_history(conv)

    register("session.fork", session_fork)

    async def session_resume(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        last_seq = params.get("last_seq")
        after_id = params.get("after_id")
        history_revision = params.get("history_revision")
        if history_revision is not None and not _is_nonneg_int(history_revision):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "history_revision must be a non-negative int")
        if after_id is not None and not _is_nonneg_int(after_id):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "after_id must be a non-negative int")
        conv = await _require_owned_conv(user_id, stored_id)

        # 重放流身份匹配且仍覆盖游标时才重放，否则回源恢复。
        revision_matches = history_revision == conv.history_revision or (
            history_revision is None and conv.history_revision == 0
        )
        if (
            revision_matches
            and isinstance(last_seq, int)
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

    async def session_history(params: dict) -> dict:
        stored_id = _require_str(params, "session_id")
        before_id = params.get("before_id")
        limit = params.get("limit", 50)
        if before_id is not None and (not _is_nonneg_int(before_id) or before_id == 0):
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "before_id must be a positive int")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "limit must be between 1 and 100")
        try:
            return await history_page(user_id, stored_id, before_id, limit)
        except ConversationNotFoundError as exc:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, str(exc)) from exc

    register("session.history", session_history)

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

    async def session_set_settings(params: dict) -> dict:
        runtime = _require_runtime(params)
        try:
            settings_patch = SessionSettingsPatch.model_validate(params.get("settings")).model_dump(exclude_unset=True)
        except ValidationError as exc:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "Invalid session parameters") from exc
        try:
            conv, settings = await update_session_settings(user_id, runtime.conversation_id, settings_patch)
        except ConversationNotFoundError as exc:
            raise JsonRpcError(JSONRPC_METHOD_NOT_FOUND, str(exc)) from exc
        runtime.settings = settings
        return {
            "session_id": runtime.session_id,
            "settings": dict(runtime.settings),
            "info": (await _runtime_info(runtime, conv)).model_dump(),
        }

    register("session.set_settings", session_set_settings)

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
                    {"command": "compress", "result": {"status": "ok", "hydrate": True, "payload": result}},
                    session_id=runtime.session_id,
                )
            return result

    register("session.compress_context", session_compress_context)

    async def session_undo_to_message(params: dict) -> dict:
        session_id = _require_str(params, "session_id")
        if not bool(params.get("confirmed")):
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                "session.undo_to_message requires confirmed=true",
                data={"requires_confirmation": True},
            )
        source_message_id = _require_nonneg_int(params, "source_message_id")
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
        raw_command = params.get("command")
        if not isinstance(raw_command, str) or not raw_command.strip():
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command must be a non-empty string")

        name = raw_command.strip().lstrip("/").lower()
        if not name:
            raise JsonRpcError(JSONRPC_INVALID_PARAMS, "command name is empty")

        cmd = resolve_slash_command(name)
        if cmd is None or cmd.handler is None:
            suggestions = suggest_commands(name)
            raise JsonRpcError(
                JSONRPC_INVALID_PARAMS,
                f"unknown command: /{name}",
                data={"suggestions": suggestions},
            )

        confirmed = bool(params.get("confirmed"))
        if cmd.requires_confirmation and not confirmed:
            raise JsonRpcError(
                JSONRPC_SLASH_CONFIRM_REQUIRED,
                f"command /{name} requires explicit confirmation",
                data={"requires_confirmation": True, "command": cmd.name},
            )

        args_raw = params.get("args")
        args_list: list[str] = []
        if isinstance(args_raw, list):
            args_list = [str(a) for a in args_raw if isinstance(a, str | int | float)]
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

    async def command_list(_params: dict) -> dict:
        """列出可用 slash 命令元数据；供客户端自动补全与确认弹窗使用。"""
        return {"commands": list_commands_for_user()}

    register("command.list", command_list)

    register_submission_handler(session, register, _require_runtime, publish_history_change)
