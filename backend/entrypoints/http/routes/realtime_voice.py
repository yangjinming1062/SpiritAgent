"""独立通话 WS 准入；账户事件仍沿普通网关交付。"""

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Query, WebSocket, WebSocketDisconnect, WebSocketException
from features.accounts.deps import account_request_scope, is_remote_session_active
from features.accounts.gateway import GatewayIdentity, consume_gateway_ticket
from features.accounts.remote.security import remote_public_base_url
from features.accounts.session_operations import is_login_active
from features.companion.proactive_runtime import user_turn_activity
from features.conversation.call_records import finish_call_record, observe_call, start_call_record
from features.conversation.session_operations import ConversationNotFoundError, get_conversation
from features.realtime_voice.contracts import (
    MAX_AUDIO_FRAME_BYTES,
    PROTOCOL_VERSION,
    SAMPLE_RATE,
    EventPayload,
    VoiceControl,
)
from features.realtime_voice.session import CALL_MANAGER, CallAlreadyActiveError, CallSession
from infrastructure.api_ids import parse_resource_id
from infrastructure.async_io import cancel_and_wait, settle_task
from infrastructure.logger import get_logger
from pydantic import ValidationError
from runtime.business_actor import BusinessActor
from runtime.chat_accounts import acquire_account_runtime, release_account_runtime
from runtime.desktop.connection import MANAGER
from runtime.presets import COMPANION_PRESET_ID
from runtime.turn_ownership import conversation_lock, release_call, reserve_call
from runtime.user_maintenance import is_user_in_maintenance
from workflows.realtime_voice import CallInputOverflowError, RealtimeVoicePipeline

from entrypoints.http.routing import get_router

logger = get_logger(__name__)
router = get_router(remote=True)
RECORD_IO_TIMEOUT = 10


async def authorized(identity: GatewayIdentity) -> bool:
    if is_user_in_maintenance(identity.user_id):
        return False
    if identity.device_id is not None:
        return await is_remote_session_active(identity.user_id, identity.device_id)
    return identity.login_record_id is not None and await is_login_active(identity.user_id, identity.login_record_id)


async def realtime_voice_identity(
    websocket: WebSocket, ticket: Annotated[str, Query(max_length=256)]
) -> GatewayIdentity:
    identity = consume_gateway_ticket(ticket)
    if identity is None or not await authorized(identity):
        raise WebSocketException(code=4401, reason="通话登录已失效")
    if identity.device_id is not None and websocket.headers.get("origin") != remote_public_base_url():
        raise WebSocketException(code=4403, reason="远程请求来源无效")
    return identity


@router.websocket("/{session_id}")
async def realtime_voice_websocket(
    websocket: WebSocket,
    session_id: str,
    identity: Annotated[GatewayIdentity, Depends(realtime_voice_identity)],
    version: int | None = Query(default=None),
) -> None:
    try:
        session_id = str(parse_resource_id(session_id))
        conv = await get_conversation(identity.user_id, session_id)
    except (ConversationNotFoundError, TypeError, ValueError):
        await websocket.close(code=4404)
        return
    if conv.system_preset_id != COMPANION_PRESET_ID or conv.is_automation or conv.parent_id is not None:
        await websocket.close(code=4403, reason="仅陪伴会话可以通话")
        return

    async def send(event: EventPayload, audio: bytes | None) -> None:
        await websocket.send_json(event)
        if audio is not None:
            await websocket.send_bytes(audio)

    session = CallSession(identity.user_id, session_id, send)
    if version != PROTOCOL_VERSION:
        await websocket.accept()
        await session.emit("error", {"message": "通话版本不匹配，请更新应用后重试", "fatal": True})
        await websocket.close(code=1002)
        return
    try:
        CALL_MANAGER.add(session)
    except CallAlreadyActiveError:
        await websocket.accept()
        await session.emit("error", {"message": "该账户已有通话", "fatal": True})
        await websocket.close(code=4409)
        return
    pipeline: RealtimeVoicePipeline | None = None
    monitor: asyncio.Task[None] | None = None
    owner = asyncio.current_task()
    record_id: int | None = None
    started = 0.0
    last_seen = 0.0
    activity = contextlib.ExitStack()

    async def monitor_identity() -> None:
        try:
            while True:
                await asyncio.sleep(5)
                if not await authorized(identity):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call authorization check failed", extra={"user_id": identity.user_id})
        if owner is not None:
            owner.cancel()

    async def finalize_record(record: int) -> None:
        async with conversation_lock(session_id):
            try:
                async with asyncio.timeout(RECORD_IO_TIMEOUT):
                    await finish_call_record(record, max(0, int((last_seen - started) * 1000)))
            finally:
                release_call(session_id, session)

    async def settle_call() -> None:
        try:
            await cancel_and_wait([monitor])
            if pipeline is not None:
                await pipeline.close()
        finally:
            try:
                if record_id is not None:
                    await finalize_record(record_id)
            finally:
                activity.close()
                release_account_runtime(identity.user_id, session.call_id)

    try:
        async with account_request_scope(identity.user_id):
            account = acquire_account_runtime(identity.user_id, session.call_id)
            try:
                await websocket.accept()
                monitor = asyncio.create_task(monitor_identity(), name="call.authorization")
                remote = identity.device_id is not None
                actor = BusinessActor(
                    identity.user_id,
                    "remote" if remote else "desktop",
                    str(identity.device_id if remote else identity.login_record_id),
                    account,
                    identity.client_context,
                    MANAGER.desktop_client_context(identity.user_id) if remote else identity.client_context,
                )
                await session.emit(
                    "session.ready", {"version": PROTOCOL_VERSION, "sample_rate": SAMPLE_RATE, "codec": "pcm_s16le"}
                )
                async with asyncio.timeout(60):
                    raw = await websocket.receive_text()
                if len(raw) > 4096:
                    raise ValueError("Invalid control frame")
                ready = VoiceControl.model_validate_json(raw)
                if ready.type == "stop":
                    return
                if ready.type != "client.ready":
                    raise ValueError("Call audio devices are not ready")
                lock = conversation_lock(session_id)
                runtime = account.runtime_sessions.get(session_id)
                if lock.locked() or (runtime is not None and runtime.busy):
                    await session.emit("error", {"message": "请等待当前会话的回复结束后再通话", "fatal": True})
                    return
                async with lock:
                    await get_conversation(identity.user_id, session_id)
                    reserve_call(session_id, session)
                started = last_seen = time.monotonic()

                async def start_record() -> int:
                    async with asyncio.timeout(RECORD_IO_TIMEOUT):
                        return await start_call_record(identity.user_id, int(session_id), datetime.now(UTC))

                # The commit may have succeeded while the socket owner was being cancelled.
                record_id = await settle_task(asyncio.create_task(start_record(), name="call.start-record"))
                if owner is not None and owner.cancelling():
                    raise asyncio.CancelledError
                pipeline = RealtimeVoicePipeline(session, actor, record_id)
                activity.enter_context(user_turn_activity(identity.user_id, enabled=True))
                session.state = "listening"
                await session.emit("session.connected")
                credit, last_frame = SAMPLE_RATE * 4, time.monotonic()
                while not session.closed.is_set():
                    async with asyncio.timeout(45):
                        message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        if message.get("code") in (1000, 1001):
                            last_seen = time.monotonic()
                        break
                    last_seen = time.monotonic()
                    if (pcm := message.get("bytes")) is not None:
                        now = time.monotonic()
                        credit = min(SAMPLE_RATE * 4, credit + (now - last_frame) * SAMPLE_RATE * 2) - len(pcm)
                        last_frame = now
                        if not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_FRAME_BYTES or credit < 0:
                            raise ValueError("Invalid audio rate or frame size")
                        try:
                            pipeline.receive_audio(pcm)
                        except CallInputOverflowError:
                            await pipeline.cancel_input()
                            await session.emit(
                                "error",
                                {"message": "语音接收过载，请重新开始发言", "fatal": False},
                            )
                        except ValueError:
                            await pipeline.cancel_input()
                            await session.emit("error", {"message": "发言过长，请缩短后重试", "fatal": False})
                        continue
                    raw = message.get("text")
                    if raw is None or len(raw) > 4096:
                        raise ValueError("Invalid control frame")
                    control = VoiceControl.model_validate_json(raw)
                    if control.type == "stop":
                        break
                    if control.type == "ping":
                        async with asyncio.timeout(RECORD_IO_TIMEOUT):
                            await observe_call(record_id, datetime.now(UTC))
                        await session.emit("pong")
                    elif control.type == "input.start":
                        await pipeline.begin_input(control.version)
                    elif control.type == "input.end":
                        await pipeline.finish_input(control.version)
                    elif control.type == "mute":
                        session.muted = control.muted
                        if control.muted:
                            # 静音只撤销未提交的麦克风内容，不中止伙伴正在说的话。
                            await pipeline.cancel_input()
                        await session.emit("state")
                    elif control.type == "audio.played":
                        await session.acknowledge_audio(control.sequence, control.version)
                    elif control.type == "part.received":
                        session.acknowledge_part(control.part_id, control.version)
            finally:
                await settle_task(asyncio.create_task(settle_call(), name="call.cleanup"))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except (ValueError, ValidationError, TimeoutError):
        logger.info("Call closed after invalid input or transport timeout", extra={"user_id": identity.user_id})
    except Exception:
        logger.exception("Call failed", extra={"user_id": identity.user_id})
    finally:
        release_call(session_id, session)
        CALL_MANAGER.remove(session)
        await session.close()
        with contextlib.suppress(RuntimeError, WebSocketDisconnect, TimeoutError):
            async with asyncio.timeout(5):
                await websocket.close(code=1000)
