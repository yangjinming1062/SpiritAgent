import asyncio
import contextlib
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from features.companion.proactive_runtime import user_turn_activity
from features.conversation.ai_routing import conversation_ai_task
from features.conversation.edit import EditNotAllowedError
from features.conversation.replacement import ReplyReplacementNotAllowedError
from features.conversation.request_contracts import ChatMessageRequest, ChatRequest
from features.conversation.retry import ReplyRetryNotAllowedError
from features.conversation.session_operations import edit_message_input, replacement_reply_input, retry_message_text
from features.conversation.submissions import (
    SubmissionConflictError,
    find_submission,
    persist_submission_input,
    record_submission_state,
    reserve_submission,
)
from features.conversation.turn_store import persist_extra_user_messages
from infrastructure.config import SETTINGS
from infrastructure.database import SESSION_LOCAL
from infrastructure.logger import get_logger
from infrastructure.request_errors import RequestError
from runtime.business_actor import BusinessActor
from runtime.conversation import ActiveTurnSnapshot, RuntimeSession
from runtime.desktop.connection import UserEventPublisher
from runtime.events.contracts import COMPANION_TURN_EVENT
from runtime.events.loop import interrupt_user_event_tasks

from workflows.chat.emitter import ChatEventEmitter
from workflows.chat.inputs import (
    _reject_read_only_session,
    _require_owned_conv,
    _require_str,
    _resolve_llm_config,
    _validate_attachments,
    conversation_history_lock,
)
from workflows.chat.orchestrator import run_chat_turn


class PublishHistoryChange(Protocol):
    def __call__(self, event_type: str, payload: object, session_id: str) -> Awaitable[None]: ...


logger = get_logger(__name__)


def create_submission_handler(
    actor: BusinessActor,
    require_runtime: Callable[[dict[str, Any]], Awaitable[RuntimeSession]],
    publish_history_change: PublishHistoryChange,
) -> Callable[[dict[str, Any]], Awaitable[dict]]:
    user_id = actor.user_id
    publisher = UserEventPublisher(user_id)
    _require_runtime = require_runtime

    async def _submit_prompt(
        params: dict,
        runtime: RuntimeSession,
        admission: contextlib.ExitStack,
        fingerprint: str,
    ) -> dict:
        response_preference = params.get("response_preference")

        edit_message_id = params.get("edit_message_id")
        retry_message_id = params.get("retry_message_id")
        replace_reply_message_id = params.get("replace_reply_message_id")
        if replace_reply_message_id is not None and any(
            key in params for key in ("text", "batch", "attachments", "edit_message_id", "retry_message_id")
        ):
            raise RequestError(422, "answer replacement reuses the original turn")
        if retry_message_id is not None and any(
            key in params for key in ("text", "batch", "attachments", "edit_message_id", "replace_reply_message_id")
        ):
            raise RequestError(422, "reply retries reuse the original message and attachments")
        if edit_message_id is not None and ("batch" in params or "attachments" in params):
            raise RequestError(
                422,
                "message edits only accept text; original attachments are retained",
            )

        precursor_user_message_ids: list[int] = []
        replacement = None
        batch = params.get("batch")
        if replace_reply_message_id is not None:
            try:
                replacement = await replacement_reply_input(user_id, runtime.session_id, replace_reply_message_id)
            except ReplyReplacementNotAllowedError as exc:
                raise RequestError(422, str(exc)) from exc
            text = replacement.text
            attachments = []
        elif retry_message_id is not None:
            try:
                text = await retry_message_text(user_id, runtime.session_id, retry_message_id)
            except ReplyRetryNotAllowedError as exc:
                raise RequestError(422, str(exc)) from exc
            attachments = []
        elif batch is not None:
            validated_batch = []
            for item in batch:
                t = item["text"]
                att = _validate_attachments(item, runtime.session_id)
                validated_batch.append({"text": t, "attachments": att})
            # 整批合为一个回合，附件上限按全批合计。
            if sum(len(item["attachments"] or ()) for item in validated_batch) > SETTINGS.max_attachments_per_turn:
                raise RequestError(
                    422,
                    f"too many attachments (max {SETTINGS.max_attachments_per_turn})",
                )

            last_item = validated_batch[-1]
            text = last_item["text"]
            attachments = last_item["attachments"]
            precursor_items = validated_batch[:-1]
            if precursor_items:
                async with SESSION_LOCAL() as db:
                    precursor_user_message_ids = await persist_extra_user_messages(
                        db,
                        runtime.conversation_id,
                        precursor_items,
                    )
        else:
            text = _require_str(params, "text")
            attachments = _validate_attachments(params, runtime.session_id)

        req = ChatRequest(
            session_id=runtime.session_id,
            message=ChatMessageRequest(content=text, attachments=attachments),
            response_preference=response_preference,
        )

        snapshot = ActiveTurnSnapshot(
            request_id=params["request_id"],
            origin_kind=actor.origin_kind,
            replace_reply_message_id=replace_reply_message_id,
        )

        emitter = ChatEventEmitter(
            publisher=publisher,
            session_id=runtime.session_id,
            active_turn=snapshot,
            snapshot_lock=runtime.snapshot_lock,
        )

        persisted_message_id: int | None = replacement.source_message_id if replacement else retry_message_id
        edited_messages: list[dict] | None = None
        if edit_message_id is not None:
            try:
                persisted_message_id, edited_messages = await edit_message_input(
                    user_id,
                    runtime.session_id,
                    runtime.conversation_id,
                    edit_message_id,
                    text,
                )
            except EditNotAllowedError as exc:
                raise RequestError(422, str(exc)) from exc
        persisted_message_id = await persist_submission_input(
            user_id,
            runtime.conversation_id,
            params["request_id"],
            req.message,
            persisted_message_id=persisted_message_id,
            precursor_ids=precursor_user_message_ids,
        )
        if edited_messages is not None:
            await publish_history_change(
                "message.edited",
                {"session_id": runtime.session_id, "messages": edited_messages},
                session_id=runtime.session_id,
            )
        snapshot.message_ids = [*precursor_user_message_ids, persisted_message_id]
        if replacement is None:
            await emitter.send_json(
                {
                    "type": "message.persisted",
                    "role": "user",
                    "message_ids": snapshot.message_ids,
                },
            )
        # 输入持久化后刷新列表，不等待回复生成。
        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
        client_context = actor.client_context
        runtime.active_turn = snapshot
        runtime.cancel_requested = False
        started = False

        async def settle_turn(status: str, error: str | None) -> None:
            async with runtime.snapshot_lock:
                snapshot.running = False
                try:
                    async with asyncio.timeout(30):
                        status, error = await record_submission_state(
                            user_id,
                            params["request_id"],
                            status,
                            message_ids=snapshot.message_ids,
                            error=error,
                        )
                        replacement_result: dict[str, object] = {}
                        if replace_reply_message_id is not None:
                            replacement_result["replace_reply_message_id"] = replace_reply_message_id
                            receipt = await find_submission(
                                user_id,
                                runtime.conversation_id,
                                params["request_id"],
                                fingerprint,
                            )
                            if receipt is not None and receipt.result_message_id is not None:
                                replacement_result.update(
                                    result_message_id=receipt.result_message_id,
                                    messages=receipt.messages,
                                )
                        await publisher.push_event("session.list_changed", {"session_id": runtime.session_id})
                        await publisher.push_event(
                            "session.state",
                            {
                                "running": False,
                                "request_id": params["request_id"],
                                "status": status,
                                "error": error,
                                **replacement_result,
                            },
                            session_id=runtime.session_id,
                        )
                finally:
                    if runtime.active_turn is snapshot:
                        runtime.active_turn = None

        async def run_turn() -> None:
            nonlocal started
            started = True
            status = "interrupted"
            error: str | None = None
            try:
                await record_submission_state(user_id, params["request_id"], "running")
                current_conv = await _require_owned_conv(user_id, req.session_id)
                llm_config = await _resolve_llm_config(user_id, conversation_ai_task(current_conv))
                await run_chat_turn(
                    req,
                    llm_config,
                    user_id,
                    emitter,
                    session_client_context=client_context,
                    track_task=actor.account.track,
                    session_settings=runtime.settings,
                    persisted_message_id=persisted_message_id,
                    final_reply_only=retry_message_id is not None or replacement is not None,
                    replace_reply_message_id=replace_reply_message_id,
                    submission_request_id=params["request_id"],
                )
                status = "failed" if emitter.failed else "completed"
                error = emitter.error
            except asyncio.CancelledError:
                if runtime.cancel_requested:
                    status = "cancelled"
                else:
                    error = "任务已中断，请核对已执行的操作后再继续。"
                raise
            except Exception:
                status = "failed"
                error = "本次回复未能完成，请稍后重试。"
                logger.exception("chat turn failed", extra={"user_id": user_id})
                await emitter.send_json(
                    {
                        "type": "error",
                        "message": error,
                        **({"retry_message_id": persisted_message_id} if replacement is None else {}),
                    },
                )
            finally:
                await settle_turn(status, error)

        runtime.chat_task = asyncio.create_task(run_turn())
        turn_activity = admission.pop_all()

        def finished(done: asyncio.Task) -> None:
            if not started and done.cancelled():
                runtime.settlement_task = asyncio.create_task(
                    settle_turn(
                        "cancelled" if runtime.cancel_requested else "interrupted",
                        None if runtime.cancel_requested else "任务已中断，请核对已执行的操作后再继续。",
                    ),
                )
                actor.account.track(runtime.settlement_task)
                runtime.settlement_task.add_done_callback(lambda _done: turn_activity.close())
            else:
                turn_activity.close()

        runtime.chat_task.add_done_callback(finished)
        actor.account.track(runtime.chat_task)
        return {
            "queued": True,
            "request_id": params["request_id"],
            "status": "accepted",
            "message_ids": snapshot.message_ids,
            "messages": snapshot.messages,
            "replace_reply_message_id": replace_reply_message_id,
        }

    async def admit_prompt(params: dict) -> dict:
        runtime = await _require_runtime(params)
        _reject_read_only_session(runtime)
        request_id = params["request_id"]
        fingerprint = hashlib.sha256(
            json.dumps(
                {key: value for key, value in params.items() if key != "request_id"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        try:
            existing = await find_submission(user_id, runtime.conversation_id, request_id, fingerprint)
            if existing is not None:
                return existing.to_result()
            with contextlib.ExitStack() as admission:
                admission.enter_context(user_turn_activity(user_id, enabled=True))
                await interrupt_user_event_tasks(user_id, COMPANION_TURN_EVENT)
                async with conversation_history_lock(runtime.session_id):
                    if runtime.busy:
                        raise RequestError(
                            409,
                            "当前会话有正在生成的回复，请稍后再试",
                            code="turn_busy",
                        )
                    await _require_owned_conv(user_id, runtime.session_id)
                    replace_reply_message_id = params.get("replace_reply_message_id")
                    receipt, created = await reserve_submission(
                        user_id,
                        runtime.conversation_id,
                        request_id,
                        fingerprint,
                        actor.origin_kind,
                        actor.origin_id,
                        replace_reply_message_id=replace_reply_message_id,
                    )
                    if not created:
                        return receipt.to_result()
                    try:
                        return await _submit_prompt(params, runtime, admission, fingerprint)
                    except BaseException:
                        if not runtime.busy:
                            runtime.active_turn = None
                        await record_submission_state(
                            user_id,
                            request_id,
                            "failed",
                            error="消息未被受理，请修改后重新发送。",
                        )
                        raise
        except SubmissionConflictError as exc:
            raise RequestError(422, str(exc)) from exc

    async def prompt_submit(params: dict) -> dict:
        try:
            session_id = str(int(params["session_id"]))
        except (TypeError, ValueError) as exc:
            raise RequestError(404, "会话不存在") from exc
        try:
            request_id = str(UUID(params["request_id"]))
        except (TypeError, ValueError) as exc:
            raise RequestError(422, "request_id must be a UUID") from exc
        params = {**params, "session_id": session_id, "request_id": request_id}
        admission = asyncio.create_task(admit_prompt(params))
        actor.account.track_admission(admission, session_id, request_id)
        return await asyncio.shield(admission)

    return prompt_submit
