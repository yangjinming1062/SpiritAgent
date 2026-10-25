import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from features.conversation.fork import SourceNotFoundError
from features.conversation.http_contracts import PromptSubmitRequest, SessionSnapshotResponse, SessionUndoResult
from features.conversation.session_operations import (
    history_snapshot,
    last_submission,
    undo_history,
)
from features.conversation.slash_commands import (
    SlashCommandContext,
    resolve_slash_command,
    suggest_commands,
)
from features.conversation.snapshots import ConversationSnapshot
from features.conversation.submissions import get_submission
from features.conversation.undo import UndoNotAllowedError
from infrastructure.async_io import cancel_tasks
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from runtime.business_actor import BusinessActor
from runtime.conversation import (
    RuntimeSession,
    new_runtime_session,
)
from runtime.desktop.connection import MANAGER, UserEventPublisher

from workflows.chat.history_commands import _do_compress_history, compression_command_result, compression_context
from workflows.chat.inputs import (
    _require_owned_conv,
    conversation_history_lock,
)
from workflows.chat.session_resources import session_runtime_info
from workflows.chat.submissions import create_submission_handler

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChatOperations:
    snapshot: Callable[[dict[str, Any]], Awaitable[dict]]
    submit: Callable[[str, PromptSubmitRequest], Awaitable[dict]]
    receipt: Callable[[dict[str, Any]], Awaitable[dict]]
    cancel: Callable[[dict[str, Any]], Awaitable[dict]]
    undo: Callable[[dict[str, Any]], Awaitable[SessionUndoResult]]
    compress: Callable[[dict[str, Any]], Awaitable[dict]]
    command: Callable[[dict[str, Any]], Awaitable[dict]]


def chat_operations(actor: BusinessActor) -> ChatOperations:
    user_id = actor.user_id
    runtime_sessions = actor.account.runtime_sessions
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

    async def _require_runtime(params: dict[str, Any]) -> RuntimeSession:
        conv = await _require_owned_conv(user_id, params["session_id"])
        return _mount_runtime(conv)

    async def _mounted_history(
        conv: ConversationSnapshot,
        *,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """在发布锁内先固定事件水位，再读取历史与在途状态。"""
        runtime = _mount_runtime(conv)
        async with runtime.snapshot_lock:
            dispatcher = (
                MANAGER.authenticated_dispatcher(
                    user_id,
                    stream_id,
                    actor.origin_kind,
                    actor.origin_id,
                )
                if stream_id
                else None
            )
            current_seq = dispatcher.replay_buffer.max_seq if dispatcher is not None else None
            conv = await _require_owned_conv(user_id, str(conv.id))
            messages, truncated, next_cursor, message_count = await history_snapshot(conv.id)
            latest_conv = await _require_owned_conv(user_id, str(conv.id))
            if latest_conv.history_revision != conv.history_revision:
                # 替换提交可能先于事件发布；持有发布锁时该回合无法收尾或开始下一次替换。
                conv = latest_conv
                messages, truncated, next_cursor, message_count = await history_snapshot(conv.id)
            submission = await last_submission(user_id, runtime.conversation_id)
            info = await session_runtime_info(user_id, runtime, conv)
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
            if dispatcher is not None:
                await dispatcher.flush_unsent()
            return SessionSnapshotResponse(
                session_id=runtime.session_id,
                message_count=message_count,
                messages=messages,
                info=info,
                current_seq=current_seq,
                stream_id=dispatcher.stream_id if dispatcher is not None else None,
                active_turn=asdict(snapshot) if snapshot is not None else None,
                last_submission=submission,
                truncated=truncated,
                next_cursor=next_cursor,
                history_revision=conv.history_revision,
            ).model_dump()

    async def publish_history_change(event_type: str, payload: object, session_id: str) -> None:
        runtime = runtime_sessions.get(session_id)
        if runtime is None:
            await publisher.push_event(event_type, payload, session_id=session_id)
            return
        async with runtime.snapshot_lock:
            await publisher.push_event(event_type, payload, session_id=session_id)

    async def session_snapshot(params: dict) -> dict:
        session_id = params["session_id"]
        conv = await _require_owned_conv(user_id, session_id)
        return await _mounted_history(
            conv,
            stream_id=params.get("stream_id"),
        )

    async def turn_receipt(params: dict) -> dict:
        conv = await _require_owned_conv(user_id, params["session_id"])
        receipt = await get_submission(user_id, conv.id, params["request_id"])
        if receipt is None:
            raise RequestError(404, "提交记录不存在", code="not_found")
        return receipt.to_result()

    async def perform_cancellation(params: dict) -> dict:
        conv = await _require_owned_conv(user_id, params["session_id"])
        session_id = str(conv.id)
        requested_id = params["request_id"]
        while pending := [
            task
            for task, identity in actor.account.admission_tasks.items()
            if identity == (session_id, requested_id) and not task.done()
        ]:
            await asyncio.gather(*(asyncio.shield(task) for task in pending), return_exceptions=True)
        runtime = runtime_sessions.get(session_id)
        cancelled = False
        if (
            runtime is not None
            and runtime.active_turn is not None
            and runtime.active_turn.request_id == requested_id
            and runtime.active_turn.running
            and runtime.chat_task is not None
            and not runtime.chat_task.done()
            and not runtime.chat_task.cancelling()
        ):
            runtime.cancel_requested = True
            cancel_tasks((runtime.chat_task,))
            cancelled = True
        return {"request_id": requested_id, "cancelled": cancelled}

    async def cancel_turn(params: dict) -> dict:
        cancellation = asyncio.create_task(perform_cancellation(params))
        actor.account.track(cancellation)
        return await asyncio.shield(cancellation)

    async def session_compress_context(params: dict) -> dict:
        runtime = await _require_runtime(params)
        async with compression_context(user_id, runtime) as conv:
            result = await _do_compress_history(conv, user_id, runtime, actor.execution_context)
            if result["compressed"]:
                await publish_history_change(
                    "command.result",
                    {"command": "compress", "result": asdict(compression_command_result(result))},
                    session_id=runtime.session_id,
                )
            return result

    async def session_undo_to_message(params: dict) -> SessionUndoResult:
        session_id = params["session_id"]
        if not bool(params.get("confirmed")):
            raise RequestError(
                409,
                "撤回消息需要明确确认",
                code="confirmation_required",
                details={"requires_confirmation": True},
            )
        source_message_id = params["source_message_id"]
        owned = await _require_owned_conv(user_id, session_id)
        session_id = str(owned.id)
        runtime = runtime_sessions.get(session_id)
        async with conversation_history_lock(session_id):
            if runtime is not None and runtime.busy:
                raise RequestError(409, "当前会话有正在生成的回复，请稍后再试", code="turn_busy")
            try:
                result = await undo_history(user_id, session_id, source_message_id)
            except (UndoNotAllowedError, SourceNotFoundError) as exc:
                raise RequestError(422, str(exc)) from exc
        await publish_history_change(
            "message.deleted",
            result.model_dump(mode="json"),
            session_id=session_id,
        )
        logger.info(
            "conversation history undone",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "source_message_id": source_message_id,
                "deleted_count": result.deleted_count,
            },
        )
        return result

    async def command_dispatch(params: dict) -> dict:
        """Slash 命令分发：按 command 查 SLASH_COMMANDS 并执行 handler，返回 {command, result} 并同步广播 command.result 事件给同 session 各窗口。"""
        runtime = await _require_runtime(params)
        raw_command = params["command"]
        if not raw_command.strip():
            raise RequestError(422, "command must be a non-empty string")

        name = raw_command.strip().lstrip("/").lower()
        if not name:
            raise RequestError(422, "command name is empty")

        cmd = resolve_slash_command(name)
        if cmd is None:
            suggestions = suggest_commands(name)
            raise RequestError(
                422,
                f"unknown command: /{name}",
                details={"suggestions": suggestions},
            )

        confirmed = bool(params.get("confirmed"))
        if cmd.requires_confirmation and not confirmed:
            raise RequestError(
                409,
                f"command /{name} requires explicit confirmation",
                code="confirmation_required",
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
            client_context=actor.execution_context,
        )

        try:
            result = await cmd.handler(ctx)
        except RequestError:
            raise
        except Exception:
            logger.exception("slash command handler failed", extra={"command": cmd.name, "user_id": user_id})
            raise RequestError(500, f"command /{name} failed unexpectedly") from None

        result_payload = {"command": cmd.name, "result": asdict(result)}
        await publish_history_change("command.result", result_payload, session_id=runtime.session_id)
        return result_payload

    return ChatOperations(
        snapshot=session_snapshot,
        submit=create_submission_handler(actor, _require_runtime, publish_history_change),
        receipt=turn_receipt,
        cancel=cancel_turn,
        undo=session_undo_to_message,
        compress=session_compress_context,
        command=command_dispatch,
    )
